"""Original Ledger permits/recovery with synthetic B18 window messages only."""

from contextlib import nullcontext

import pytest

from tests.test_wechat_background_control import Rig
from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat.background_contract import CONTROL_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def prepare(ledger, marker=CONTROL_MARKER):
    store, state = BridgeStore(ledger), SendState(ledger)
    store.bind(BINDING, mode="draft")
    ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
    action_id = state.queue_marker(BINDING, "background-control", marker)
    action = ledger.claim(action_id)
    operation = state.begin(BINDING, action, {"selected": Cursor(0, 0)})
    return state, operation, action


def journal(ledger):
    state, operation, action = prepare(ledger)
    return BackgroundIntentJournal(state, operation, BINDING, action), state, operation, action


def test_control_uses_original_single_input_and_submit_permits(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        recorder, state, operation, action = journal(ledger)
        rig = Rig()
        control = rig.control()
        control.action = action
        control.record = recorder.record
        result = control.run_once(write_guard=nullcontext())
        assert result.state == "REQUESTED" and not result.delivery_verified
        row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
        assert row["input_permitted"] == row["submit_permitted"] == 1
        stages = recorder._stages(ledger._db)
        assert len([s for s in stages if s.startswith("BG_CHAR_")]) == recorder.units
        with pytest.raises(SocialError, match="already_used"):
            BackgroundIntentJournal(state, operation, BINDING, action)
        state.finish(operation, reaped=True, restored=True, reason="synthetic_background_requested")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic_background_requested")
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
        with pytest.raises(SocialError):
            state.queue_marker(BINDING, "another-request", CONTROL_MARKER)


@pytest.mark.parametrize("stage_count", [0, 1, 2, 20, *range(
    len(CONTROL_MARKER.encode("utf-16-le")) // 2 + 1,
    len(CONTROL_MARKER.encode("utf-16-le")) // 2 + 5)])
def test_crash_and_reopen_never_issue_another_permit(tmp_path, stage_count):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        recorder, _, operation, action = journal(ledger)
        sequence = ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * recorder.units + ["MOVE_INTENT", "DOWN_INTENT", "UP_INTENT"]
        for stage in sequence[:stage_count]:
            recorder.record(stage)
    with Ledger(path) as ledger:
        state = SendState(ledger)
        row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
        assert row["finished"] and row["phase"] == "INTERRUPTED"
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
        with pytest.raises(SocialError):
            BackgroundIntentJournal(state, operation, BINDING, action)
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] == 1


@pytest.mark.parametrize("phase", ["input", "submit"])
def test_pause_stops_further_effects_but_allows_focus_cleanup(tmp_path, phase):
    with Ledger(tmp_path / "ledger.db") as ledger:
        recorder, _, _, _ = journal(ledger)
        recorder.record("PSEUDO_FOCUS_INTENT")
        if phase == "submit":
            for _ in range(recorder.units):
                recorder.record("CHAR_INTENT")
        ledger.pause(True)
        with pytest.raises(SocialError):
            recorder.record("CHAR_INTENT" if phase == "input" else "MOVE_INTENT")
        assert recorder.record("PSEUDO_FOCUS_END_INTENT")
        with pytest.raises(SocialError):
            recorder.record("PSEUDO_FOCUS_END_INTENT")
        row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
        assert row["input_permitted"] == 1 and row["submit_permitted"] == 0


def test_stage_order_and_cleanup_are_irreversible(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        recorder, _, _, _ = journal(ledger)
        recorder.record("PSEUDO_FOCUS_INTENT")
        with pytest.raises(SocialError, match="submit_order"):
            recorder.record("MOVE_INTENT")
        with pytest.raises(SocialError, match="prior_failure"):
            recorder.record("CHAR_INTENT")
        assert recorder.record("PSEUDO_FOCUS_END_INTENT")


def test_only_exact_unicode_marker_passes_existing_queue_gate(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        store, state = BridgeStore(ledger), SendState(ledger)
        store.bind(BINDING, mode="draft")
        ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
        for marker in (CONTROL_MARKER + "extra", "W2B-任意中文", "W2B-🙂", CONTROL_MARKER.replace("中文", "其他")):
            with pytest.raises(SocialError, match="invalid_fixed_test_marker"):
                state.queue_marker(BINDING, "bad", marker)
