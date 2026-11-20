import hashlib
import sqlite3
from contextlib import nullcontext
from dataclasses import replace

import pytest

from scripts.wechat_background_control import StandaloneDeferredControl, StandaloneValueControl
from scripts.wechat_standalone_backend import StandalonePipe
from tests.test_wechat_background_control import ACTION, BINDING, Rig
from tests.test_wechat_background_journal import prepare
from wechat_agent.bridges.wechat.background_contract import DEFERRED_MARKER, VALUE_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def setup(*, fault=None, record=None, deferred=False):
    rig = Rig()
    marker = DEFERRED_MARKER if deferred else VALUE_MARKER
    action = replace(ACTION, text=marker)
    calls = []

    def value(token, text):
        assert token == rig.token and text == marker
        if deferred:
            assert not rig.posts  # No pseudo-focus before writing the complete value.
        assert not rig.body
        calls.append(text)
        rig.body = text.replace("🙂", "") if fault == "missing_emoji" else text
        if fault == "unknown":
            return False
        if fault == "exception":
            raise SocialError("synthetic_value_failure")
        return True

    rig.set_value_once = value
    control_type = StandaloneDeferredControl if deferred else StandaloneValueControl
    control = control_type(BINDING, action, observe=rig.observe, guard=rig.guard,
        transport=rig, record=record or rig.record, clock=lambda: rig.now, sleep=rig.sleep)
    return control, rig, calls


def test_complete_unicode_is_one_value_call_and_keeps_original_pseudo_focus_and_click():
    control, rig, calls = setup()
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and not result.delivery_verified
    assert calls == [VALUE_MARKER]
    assert [p[0] for p in rig.posts] == [7, 0x200, 0x201, 0x202, 8]
    assert rig.intents == ["PSEUDO_FOCUS_INTENT", "VALUE_INPUT_INTENT", "MOVE_INTENT",
                           "DOWN_INTENT", "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]
    assert result.focus_cleanup_verified
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())


@pytest.mark.parametrize("fault", ["missing_emoji", "unknown", "exception"])
def test_partial_or_uncertain_value_never_clears_retries_or_clicks(fault):
    control, rig, calls = setup(fault=fault)
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and len(calls) == 1
    assert [p[0] for p in rig.posts] == [7, 8]
    assert rig.body


def test_value_intent_rejection_does_not_write_and_still_ends_owned_pseudo_focus():
    control, rig, calls = setup()
    rig.fail_record = "VALUE_INPUT_INTENT"
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and not calls
    assert [p[0] for p in rig.posts] == [7, 8]


def test_complete_value_intent_uses_original_ledger_single_input_submit_permits(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, VALUE_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        control, rig, calls = setup(record=journal.record)
        control.action = action
        result = control.run_once(write_guard=nullcontext())
        assert result.state == "REQUESTED" and calls == [VALUE_MARKER]
        stages = journal._stages(ledger._db)
        assert stages == ["BG_PSEUDO_FOCUS_INTENT", "BG_VALUE_INPUT_INTENT", "BG_MOVE_INTENT",
                          "BG_DOWN_INTENT", "BG_UP_INTENT", "BG_PSEUDO_FOCUS_END_INTENT"]
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()) == (1, 1)


def test_value_receiver_requires_explicit_mode_before_touching_pipe():
    pipe = StandalonePipe.__new__(StandalonePipe)
    pipe.allow_fixed_input, pipe.input_used = False, False
    with pytest.raises(SocialError, match="denied"):
        pipe.set_value_once("a" * 64, VALUE_MARKER)


def test_old_failed_input_ledger_and_body_hash_can_be_reconciled_without_text_storage():
    from wechat_agent.bridges.wechat.background_contract import CONTROL_MARKER

    without_emoji = CONTROL_MARKER.replace("🙂", "")
    assert len(without_emoji.encode("utf-16-le")) // 2 == 34
    assert hashlib.sha256(without_emoji.encode()).hexdigest() == "2cf3fa90d7cc0ae004d1933f43aba856c5bb8e2f90e091fc84dae1d1a89fbcf7"
    assert hashlib.sha256(CONTROL_MARKER.encode()).hexdigest() != hashlib.sha256(without_emoji.encode()).hexdigest()


def test_value_crash_after_input_permit_never_replays(tmp_path):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        state, operation, action = prepare(ledger, VALUE_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        journal.record("PSEUDO_FOCUS_INTENT")
        journal.record("VALUE_INPUT_INTENT")
    with Ledger(path) as ledger:
        row = ledger._db.execute("SELECT input_permitted,submit_permitted,finished FROM wechat_send_operations").fetchone()
        assert tuple(row) == (1, 0, 1)
        with pytest.raises(SocialError):
            BackgroundIntentJournal(type(state)(ledger), operation, BINDING, action)
    # The persisted body is the exact fixed marker; no alternate send attempt.
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] == 1


@pytest.mark.parametrize("damage", [None, "partial", "submit", "wrong_body"])
def test_only_named_complete_input_only_first_background_control_can_be_accepted(tmp_path, damage):
    from wechat_agent.bridges.wechat import channel_review

    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        journal.record("PSEUDO_FOCUS_INTENT")
        for _ in range(journal.units - (1 if damage == "partial" else 0)):
            journal.record("CHAR_INTENT")
        journal.record("PSEUDO_FOCUS_END_INTENT")
        state.finish(operation, reaped=True, restored=True, reason="synthetic_input_only_failure")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic_input_only_failure")
        if damage == "submit":
            ledger._db.execute("UPDATE wechat_send_operations SET submit_permitted=1")
        if damage == "wrong_body":
            ledger._db.execute("UPDATE outbox SET text='W2B-任意中文'")

        def review():
            return channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))

        if damage:
            with pytest.raises(SocialError, match="uncertainty_source_invalid"):
                review()
        else:
            assert len(review()) == 64


def test_deferred_focus_is_after_one_complete_value_and_before_mouse_intents():
    control, rig, calls = setup(deferred=True)
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and calls == [DEFERRED_MARKER]
    assert rig.intents == ["VALUE_INPUT_INTENT", "PSEUDO_FOCUS_INTENT", "MOVE_INTENT",
                           "DOWN_INTENT", "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]
    assert [p[0] for p in rig.posts] == [7, 0x200, 0x201, 0x202, 8]


def test_deferred_mismatch_never_creates_pseudo_focus_or_click():
    control, rig, calls = setup(deferred=True, fault="missing_emoji")
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and calls == [DEFERRED_MARKER]
    assert not rig.posts and rig.body


def test_deferred_journal_grants_input_on_value_and_does_not_grant_it_again_for_focus(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, DEFERRED_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        control, rig, calls = setup(deferred=True, record=journal.record)
        control.action = action
        assert control.run_once(write_guard=nullcontext()).state == "REQUESTED"
        assert calls == [DEFERRED_MARKER]
        assert journal._stages(ledger._db) == ["BG_VALUE_INPUT_INTENT", "BG_PSEUDO_FOCUS_INTENT",
            "BG_MOVE_INTENT", "BG_DOWN_INTENT", "BG_UP_INTENT", "BG_PSEUDO_FOCUS_END_INTENT"]
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()) == (1, 1)


def test_value_foreground_unknown_exception_is_narrow_and_requires_no_submit(tmp_path):
    from wechat_agent.bridges.wechat import channel_review

    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, VALUE_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        journal.record("PSEUDO_FOCUS_INTENT")
        journal.record("VALUE_INPUT_INTENT")
        state.finish(operation, reaped=True, restored=True, reason="background_target_became_foreground")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "background_target_became_foreground")
        assert len(channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))) == 64
        ledger._db.execute("UPDATE wechat_send_operations SET submit_permitted=1")
        with pytest.raises(SocialError):
            channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))
