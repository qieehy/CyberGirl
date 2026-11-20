"""Actual child pipes plus SQLite commits, only synthetic native effects."""

import asyncio
import io
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wechat_native_sender import ACTION, BINDING
from tests.test_wechat_sender import Memory, baselines
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.directed_click import ClickResult, DirectedClick
from wechat_agent.bridges.wechat.gesture_protocol import GestureIntentClient
from wechat_agent.bridges.wechat.send_process import JsonSendWorker, python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import AccessibilityLease, WeChatSendAdapter
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.social.service import SocialService

NOW = 1_800_000_000


class NoModel:
    async def aclose(self):
        pass

    async def decide(self, *args, **kwargs):
        raise AssertionError("gesture validation must not invoke a model")


def setup(ledger):
    BridgeStore(ledger).bind(BINDING, mode="draft")
    ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
    state = SendState(ledger)
    action_id = state.queue_marker(BINDING, "synthetic-durable-gesture", ACTION.text)
    return state, action_id


@pytest.mark.parametrize("mode,fail_stage", [("normal", None), ("down-fail", None), ("up-fail", None),
    ("normal", "CLICK_BEGIN_INTENT"), ("normal", "CLICK_DOWN_INTENT"), ("normal", "CLICK_UP_INTENT"),
    ("up-fail", "CLICK_CANCEL_INTENT"), ("normal", "AFTER_DOWN_ALL"), ("hang-down", None)])
def test_private_worker_waits_for_owner_commit_and_never_confirms_delivery(tmp_path, mode, fail_stage):
    async def scenario():
        path, trace = tmp_path / "ledger.db", tmp_path / "trace.jsonl"
        with Ledger(path, now=lambda: NOW) as ledger:
            state, action_id = setup(ledger)
            if fail_stage:
                condition = ("NEW.stage LIKE 'CLICK_%' AND NEW.stage NOT IN ('CLICK_BEGIN_INTENT','CLICK_DOWN_INTENT')"
                             if fail_stage == "AFTER_DOWN_ALL" else f"NEW.stage='{fail_stage}'")
                ledger._db.execute(f"""CREATE TRIGGER fail_gesture BEFORE INSERT ON wechat_send_journal
                    WHEN {condition} BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END""")
            command = python_worker_command(Path(__file__).with_name("wechat_gesture_worker.py"), mode, str(trace))
            workers = []

            def factory():
                worker = JsonSendWorker(command)
                workers.append(worker)
                return worker

            adapter = WeChatSendAdapter(ledger, BINDING, worker_factory=factory,
                lease_factory=lambda window, record: AccessibilityLease(Memory(), window, record), baseline_provider=baselines,
                preparation_timeout=15, timeout=0.7 if mode == "hang-down" else 7)
            original_intent = adapter.state.gesture_intent

            def committed(*args):
                original_intent(*args)
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as readonly:
                    assert readonly.execute("SELECT stage FROM wechat_send_journal ORDER BY seq DESC LIMIT 1").fetchone()[0] == args[-1]
                with trace.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"stage": "committed-" + args[-1]}) + "\n")

            adapter.state.gesture_intent = committed
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                result = await service.dispatch(action_id)
            assert result["state"] == "UNCONFIRMED"
            assert workers[0].process.returncode is not None
            op = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
            assert op["worker_reaped"] and op["restore_verified"] and op["submit_permitted"]
            summary = state.gesture_summary(op["operation_id"])
            stages = summary["stages"]
            history = [json.loads(s)["stage"] for s in trace.read_text().splitlines()] if trace.exists() else []
            effects = [s for s in history if not s.startswith("committed-")]
            if fail_stage == "CLICK_BEGIN_INTENT":
                assert effects == [] and summary["state"] == "NOT_STARTED"
            elif fail_stage == "CLICK_DOWN_INTENT":
                assert effects == ["observe"] and summary["state"] == "NOT_STARTED"
            else:
                assert effects[0] == "observe" and "down" in effects
                assert summary["state"] == ("REQUESTED" if mode == "normal" and fail_stage is None else "UNKNOWN")
                assert ("up" in effects) == (mode not in {"down-fail", "hang-down"}
                    and fail_stage not in {"CLICK_UP_INTENT", "AFTER_DOWN_ALL"})
            for effect, intention in [("observe", "CLICK_BEGIN_INTENT"), ("down", "CLICK_DOWN_INTENT"), ("up", "CLICK_UP_INTENT")]:
                if effect in effects:
                    assert history.index("committed-" + intention) < history.index(effect)
            assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
            # Separate owner-independent read sees all committed stages after child exit.
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as readonly:
                assert [r[0] for r in readonly.execute("SELECT stage FROM wechat_send_journal WHERE stage LIKE 'CLICK_%' ORDER BY seq")] == stages
            if fail_stage:
                ledger._db.execute("DROP TRIGGER fail_gesture")
            with pytest.raises(SocialError):
                state.gesture_intent(op["operation_id"], BINDING, replace(ACTION, action_id=action_id), "CLICK_BEGIN_INTENT")
    asyncio.run(scenario())


def test_commit_precedes_ack_and_effect_is_not_reached_when_ack_is_wrong():
    request = {"operation_id": "fixture", "sequence": 7, "command": "submit_once"}
    ack = {"operation_id": "different", "sequence": 7,
           "intent_ack": {"stage": "CLICK_BEGIN_INTENT", "ordinal": 1, "accepted": True}}
    client = GestureIntentClient(io.BytesIO(json.dumps(ack).encode() + b"\n"), io.BytesIO())
    client.context(request)
    with pytest.raises(SocialError, match="ack_invalid"):
        client.record("CLICK_BEGIN_INTENT")


@pytest.mark.parametrize("mutation", ["operation", "sequence", "ordinal", "stage", "command"])
def test_parent_rejects_unbound_or_out_of_phase_intent_before_journal(mutation):
    class Pipe:
        def write(self, raw):
            pass

        async def drain(self):
            pass

        async def readuntil(self, delimiter):
            return json.dumps(response).encode() + b"\n"

    response = {"operation_id": "fixture", "sequence": 1,
                "intent": {"stage": "CLICK_BEGIN_INTENT", "ordinal": 1}}
    command = "submit_once"
    if mutation == "operation":
        response["operation_id"] = "other"
    elif mutation == "sequence":
        response["sequence"] = True
    elif mutation == "ordinal":
        response["intent"]["ordinal"] = 2
    elif mutation == "stage":
        response["intent"]["stage"] = []
    else:
        command = "input_once"
    parent = JsonSendWorker(("synthetic-worker",))
    parent.operation_id = "fixture"
    recorded = []
    parent.bind_gesture_journal(recorded.append, lambda result: None)
    parent.process = SimpleNamespace(stdin=Pipe(), stdout=Pipe())
    with pytest.raises(SocialError, match="gesture_intent_request_invalid"):
        asyncio.run(parent._rpc(command))
    assert recorded == []


def test_unfinished_durable_down_recovers_unknown_and_submit_permission_is_not_reused(tmp_path):
    path = tmp_path / "ledger.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        state, action_id = setup(ledger)
        action = ledger.claim(action_id)
        op = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(op, BINDING, action, "INPUT_PERMITTED")
        state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
        assert state.gesture_summary(op)["state"] == "UNKNOWN"
        with pytest.raises(SocialError, match="not_found"):
            state.gesture_summary("missing-operation")
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_UP_INTENT")
        state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        state.gesture_intent(op, BINDING, action, "CLICK_DOWN_INTENT")
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        ledger.pause(True)
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_UP_INTENT")
        # A revoked action may still append a cleanup intention, never another UP.
        state.gesture_intent(op, BINDING, action, "CLICK_CANCEL_INTENT")
    with Ledger(path, now=lambda: NOW) as ledger:
        state = SendState(ledger)
        summary = state.gesture_summary(op)
        assert summary["state"] == "UNKNOWN" and summary["may_have_reached_target"]
        assert summary["message_mode"] == "unobserved"  # No terminal mode proof after interruption.
        assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
        assert ledger.claim(action_id) is None
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_UP_INTENT")


def test_begin_disk_failure_consumes_single_use_before_first_observation():
    from tests.test_wechat_directed_click import snapshot

    before, initial = snapshot()
    observed = []

    def fail(stage):
        raise OSError("synthetic durable failure")

    gesture = DirectedClick(BINDING, ACTION, initial, observe=lambda: observed.append(True) or before,
        emit=lambda *args: True, cancel=lambda *args: True, record=fail)
    assert gesture.perform_once().state == "NOT_STARTED" and observed == []
    with pytest.raises(SocialError, match="already_used"):
        gesture.perform_once()


def test_result_cannot_turn_partial_down_into_requested_or_not_started(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        state, action_id = setup(ledger)
        action = ledger.claim(action_id)
        op = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(op, BINDING, action, "INPUT_PERMITTED")
        state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
        with pytest.raises(SocialError, match="fixed_marker_required"):
            state.gesture_intent(op, BINDING, replace(action, text="ordinary model text"), "CLICK_BEGIN_INTENT")
        ledger._db.execute("UPDATE inbox SET origin='source'")
        with pytest.raises(SocialError, match="operation_mismatch"):
            state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        ledger._db.execute("UPDATE inbox SET origin='operator'")
        state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        state.gesture_intent(op, BINDING, action, "CLICK_DOWN_INTENT")
        with pytest.raises(SocialError):
            state.gesture_result(op, BINDING, action, ClickResult("NOT_STARTED", "rejected", False, False, False, False))
        with pytest.raises(SocialError):
            state.gesture_result(op, BINDING, action, ClickResult("REQUESTED", "requested", True, True, False, False))


@pytest.mark.parametrize("mode,fail_stage,expected", [
    ("posted-normal", None, ["move", "down", "up"]),
    ("posted-move-fail", None, ["move"]),
    ("posted-up-fail", None, ["move", "down", "up", "cancel"]),
    ("posted-press-missing", None, ["move", "down", "cancel"]),
    ("posted-normal", "CLICK_MOVE_INTENT", []),
    ("posted-normal", "CLICK_DOWN_INTENT", ["move"]),
    ("posted-normal", "PAUSE_AFTER_MOVE", ["move"]),
    ("posted-normal", "CLICK_UP_INTENT", ["move", "down", "cancel"]),
    ("posted-up-fail", "CLICK_CANCEL_INTENT", ["move", "down", "up", "cancel"]),
    ("posted-hang-move", None, ["move"]),
    ("sequence-normal", None, ["move", "down", "up"]),
    ("sequence-move-fail", None, ["move"]),
    ("sequence-up-fail", None, ["move", "down", "up", "cancel"]),
    ("sequence-normal", "CLICK_DOWN_INTENT", []),
    ("sequence-normal", "CLICK_UP_INTENT", []),
    ("sequence-normal", "PAUSE_AFTER_MOVE", []),
    ("sequence-hang-move", None, ["move"]),
])
def test_queued_worker_commits_move_and_up_is_not_granted_without_current_press(tmp_path, mode, fail_stage, expected):
    async def scenario():
        path, trace = tmp_path / "posted.db", tmp_path / "posted.jsonl"
        with Ledger(path, now=lambda: NOW) as ledger:
            state, action_id = setup(ledger)
            if fail_stage:
                ledger._db.execute(f"""CREATE TRIGGER fail_posted BEFORE INSERT ON wechat_send_journal
                    WHEN NEW.stage='{fail_stage}' BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END""")
            workers = []

            def factory():
                worker = JsonSendWorker(python_worker_command(Path(__file__).with_name("wechat_gesture_worker.py"), mode, str(trace)))
                workers.append(worker)
                return worker

            adapter = WeChatSendAdapter(ledger, BINDING, worker_factory=factory,
                lease_factory=lambda window, record: AccessibilityLease(Memory(), window, record),
                baseline_provider=baselines, preparation_timeout=15,
                timeout=0.7 if mode in {"posted-hang-move", "sequence-hang-move"} else 7)
            original = adapter.state.gesture_intent

            def committed(*args):
                if fail_stage == "PAUSE_AFTER_MOVE" and args[-1] == "CLICK_DOWN_INTENT":
                    ledger.pause(True)
                original(*args)
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as readonly:
                    assert readonly.execute("SELECT stage FROM wechat_send_journal ORDER BY seq DESC LIMIT 1").fetchone()[0] == args[-1]
                with trace.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"stage": "committed-" + args[-1]}) + "\n")

            adapter.state.gesture_intent = committed
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                result = await service.dispatch(action_id)
            assert result["state"] == "UNCONFIRMED" and workers[0].process.returncode is not None
            op = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
            assert op["finished"] and op["worker_reaped"] and op["restore_verified"]
            summary = state.gesture_summary(op["operation_id"])
            history = [json.loads(s)["stage"] for s in trace.read_text().splitlines()]
            assert [e for e in history if e in {"move", "down", "up", "cancel"}] == expected
            for effect, stage in (("move", "CLICK_MOVE_INTENT"), ("down", "CLICK_DOWN_INTENT"), ("up", "CLICK_UP_INTENT")):
                if effect in expected:
                    assert history.index("committed-" + stage) < history.index(effect)
            assert summary["state"] == ("NOT_STARTED" if fail_stage == "CLICK_MOVE_INTENT" else
                                        "REQUESTED" if mode in {"posted-normal", "sequence-normal"} and not fail_stage else "UNKNOWN")
            assert summary["message_mode"] == "queued"
            if expected:
                assert summary["may_have_reached_target"]
            assert summary["cancel_enqueued"] == ("cancel" in expected)
            assert not summary["cancel_acknowledged"]
            if mode in {"posted-normal", "sequence-normal"} and not fail_stage:
                # A completed queued gesture still grants no delivery confirmation or retry.
                assert ledger.claim(action_id) is None
    asyncio.run(scenario())


def test_interruption_after_durable_move_is_unknown_and_cannot_be_replayed(tmp_path):
    path = tmp_path / "move-crash.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        state, action_id = setup(ledger)
        action = ledger.claim(action_id)
        op = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(op, BINDING, action, "INPUT_PERMITTED")
        state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
        state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        state.gesture_intent(op, BINDING, action, "CLICK_MOVE_INTENT")
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_MOVE_INTENT")
        with pytest.raises(SocialError, match="conflicts"):
            state.gesture_result(op, BINDING, action, ClickResult("NOT_STARTED", "hidden_move", False, False, False, False))
        ledger.pause(True)
        with pytest.raises(SocialError):
            state.gesture_intent(op, BINDING, action, "CLICK_DOWN_INTENT")
    with Ledger(path, now=lambda: NOW) as ledger:
        summary = SendState(ledger).gesture_summary(op)
        assert summary["state"] == "UNKNOWN" and summary["message_mode"] == "queued" and summary["may_have_reached_target"]
        assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED" and ledger.claim(action_id) is None


def test_move_cannot_be_inserted_after_down_and_queued_result_cannot_omit_move(tmp_path):
    with Ledger(tmp_path / "order.db", now=lambda: NOW) as ledger:
        state, action_id = setup(ledger)
        action = ledger.claim(action_id)
        op = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(op, BINDING, action, "INPUT_PERMITTED")
        state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
        state.gesture_intent(op, BINDING, action, "CLICK_BEGIN_INTENT")
        state.gesture_intent(op, BINDING, action, "CLICK_DOWN_INTENT")
        with pytest.raises(SocialError, match="out_of_order"):
            state.gesture_intent(op, BINDING, action, "CLICK_MOVE_INTENT")
        with pytest.raises(SocialError, match="conflicts"):
            state.gesture_result(op, BINDING, action,
                ClickResult("UNKNOWN", "missing_move", True, False, False, False, move_attempted=True, message_mode="queued"))


def test_sixth_worker_intent_is_rejected_before_another_parent_commit():
    stages = ["CLICK_BEGIN_INTENT", "CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT", "CLICK_CANCEL_INTENT", "CLICK_BEGIN_INTENT"]

    class Pipe:
        def __init__(self):
            self.ordinal = 0

        def write(self, raw):
            pass

        async def drain(self):
            pass

        async def readuntil(self, delimiter):
            self.ordinal += 1
            return json.dumps({"operation_id": "fixture", "sequence": 1,
                "intent": {"stage": stages[self.ordinal - 1], "ordinal": self.ordinal}}).encode() + b"\n"

    worker = JsonSendWorker(("synthetic",))
    worker.operation_id = "fixture"
    recorded = []
    worker.bind_gesture_journal(recorded.append, lambda result: None)
    worker.process = SimpleNamespace(stdin=Pipe(), stdout=Pipe())
    with pytest.raises(SocialError, match="request_invalid"):
        asyncio.run(worker._rpc("submit_once"))
    assert recorded == stages[:5]
