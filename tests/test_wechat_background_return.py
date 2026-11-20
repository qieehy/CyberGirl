"""Bound-window Return submission with synthetic facts and the original ledger."""

from contextlib import nullcontext
from dataclasses import replace

import pytest

from scripts.wechat_background_control import StandaloneReturnControl
from tests.test_wechat_background_control import ACTION, BINDING, Rig
from tests.test_wechat_background_journal import prepare
from tests.test_wechat_background_native import Function
from tests.test_wechat_background_native import setup as native_setup
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_contract import BMP_MARKER, RETURN_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def setup(*, record=None):
    rig = Rig()
    original = rig.post

    def post(message, wparam, lparam):
        result = original(message, wparam, lparam)
        if message == 0x100:
            assert wparam == 0x0D and lparam == 0x001C0001
            assert rig.body == RETURN_MARKER  # The complete marker precedes submit.
            rig.body = ""
        if message == 0x101:
            assert wparam == 0x0D and lparam == 0xC01C0001
        return result

    rig.post = post
    control = StandaloneReturnControl(BINDING, replace(ACTION, text=RETURN_MARKER),
        observe=rig.observe, guard=rig.guard, transport=rig, record=record or rig.record,
        clock=lambda: rig.now, sleep=rig.sleep)
    return control, rig


def test_return_uses_two_target_window_messages_and_never_calls_mouse_or_value():
    control, rig = setup()
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and result.submit_possible and not result.delivery_verified
    assert rig.payload.decode("utf-16-le") == RETURN_MARKER
    assert [p[0] for p in rig.posts] == [7] + [0x102] * len(RETURN_MARKER) + [0x100, 0x101, 8]
    assert rig.intents[-3:] == ["RETURN_DOWN_INTENT", "RETURN_UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())


@pytest.mark.parametrize("stage", ["RETURN_DOWN_INTENT", "RETURN_UP_INTENT"])
def test_key_intent_failure_never_posts_either_key(stage):
    control, rig = setup()
    rig.fail_record = stage
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and not result.submit_possible
    assert result.reason == "background_return_intent_not_committed"
    assert all(p[0] not in {0x100, 0x101, 0x200, 0x201, 0x202} for p in rig.posts)
    assert rig.body == RETURN_MARKER


def test_return_ledger_uses_once_only_submit_and_rejects_mouse_route(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, RETURN_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        control, rig = setup(record=journal.record)
        control.action = action
        assert control.run_once(write_guard=nullcontext()).state == "REQUESTED"
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()) == (1, 1)
        assert journal._stages(ledger._db)[-3:] == ["BG_RETURN_DOWN_INTENT", "BG_RETURN_UP_INTENT", "BG_PSEUDO_FOCUS_END_INTENT"]
        with pytest.raises(SocialError, match="submit_method_denied"):
            journal.record("MOVE_INTENT")


def test_foreground_guard_after_key_down_keeps_exact_reason_and_never_retries():
    control, rig = setup()
    seen = [False]
    rig.after_post = lambda message: seen.__setitem__(0, seen[0] or message == 0x100)

    def guard():
        if seen[0]:
            raise SocialError("background_target_became_foreground")
        return True

    control.guard = guard
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and result.submit_possible
    assert result.reason == "background_target_became_foreground"
    assert [p[0] for p in rig.posts].count(0x100) == 1
    assert all(p[0] not in {0x101, 0x200, 0x201, 0x202} for p in rig.posts)


@pytest.mark.parametrize("bad", ["mode", "key", "scan", "modifier", "float_message"])
def test_native_return_is_exact_opt_in_and_refuses_held_modifiers(monkeypatch, bad):
    capability, state = native_setup(monkeypatch)
    capability.return_mode = bad != "mode"
    capability.user.GetAsyncKeyState = Function(lambda _: 0x8000 if bad == "modifier" else 0)
    with pytest.raises(SocialError):
        capability.post(256.0 if bad == "float_message" else 0x100,
                        0x41 if bad == "key" else 0x0D, 0 if bad == "scan" else 0x001C0001)
    assert state["posted"] == 0


def test_native_return_checks_canonical_binding_before_actual_enqueue(monkeypatch):
    capability, state = native_setup(monkeypatch)
    capability.return_mode = True
    capability.user.GetAsyncKeyState = Function(lambda _: 0)
    assert capability.post(0x100, 0x0D, 0x001C0001)
    assert capability.post(0x101, 0x0D, 0xC01C0001)
    assert state["posted"] == 2 and state["native_checks"] >= 2


def test_named_previous_bmp_submit_uncertainty_is_narrow_and_does_not_change_status(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, BMP_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        for stage in ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * len(BMP_MARKER) + ["MOVE_INTENT", "DOWN_INTENT", "UP_INTENT"]:
            journal.record(stage)
        state.finish(operation, reaped=True, restored=True, reason="background_target_became_foreground")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic")
        assert len(channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))) == 64
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
        ledger._db.execute("DELETE FROM wechat_send_journal WHERE stage='BG_UP_INTENT'")
        with pytest.raises(SocialError, match="uncertainty_source_invalid"):
            channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))
