"""Accepted printable-BMP scope, without any actual WeChat input."""

from contextlib import nullcontext
from dataclasses import replace

import pytest

from scripts.wechat_background_control import StandaloneBmpControl
from scripts.wechat_standalone_worker import StandaloneSession
from tests.test_wechat_background_control import ACTION, BINDING, Rig
from tests.test_wechat_background_journal import prepare
from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_contract import BMP_MARKER, BMP_REQUEST, SOURCE_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.background_text import require_background_bmp_text
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("text", ["你好，世界！", "English 123, punctuation.", "中文 English：测试。", "éΩ日文", "中" * 96])
def test_supported_text_is_returned_exactly_without_normalizing_or_dropping_characters(text):
    assert require_background_bmp_text(text) == text


@pytest.mark.parametrize("text", ["你好🙂", "🚀", "𠮷", "中" * 97, "", "   ", "a\r", "a\n", "a\t", "a\x00",
                                  "a\x1b", "a\u2028b", "a\u2029b", "a\u202eb", "a\u200db", "\ud83d", "\ude42", None])
def test_unsupported_body_is_rejected_before_even_an_observation_or_permit(text):
    rig = Rig()

    def forbidden(*_args):
        raise AssertionError("no observation, input or intent may occur")

    with pytest.raises(SocialError, match="unsupported"):
        StandaloneBmpControl(BINDING, replace(ACTION, text=text), observe=forbidden, guard=forbidden,
                              transport=rig, record=forbidden)
    assert not rig.posts and not rig.intents and rig.body == ""


def bmp_control(rig, *, record=None):
    return StandaloneBmpControl(BINDING, replace(ACTION, text=BMP_MARKER),
        observe=rig.observe, guard=rig.guard, transport=rig, record=record or rig.record,
        clock=lambda: rig.now, sleep=rig.sleep)


def test_bmp_route_uses_unchanged_message_input_and_verifies_complete_chinese_before_one_click():
    rig = Rig()
    control = bmp_control(rig)

    def no_value(*_args):
        raise AssertionError("BMP route must never fall back to UIA SetValue")

    rig.set_value_once = no_value
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and not result.delivery_verified
    assert rig.payload.decode("utf-16-le") == BMP_MARKER
    assert [message for message, _, _ in rig.posts] == [7] + [0x102] * len(BMP_MARKER) + [0x200, 0x201, 0x202, 8]
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())


def test_bmp_real_ledger_intents_use_one_input_one_submit_and_never_replay(tmp_path):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        state, operation, action = prepare(ledger, BMP_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        rig = Rig()
        control = bmp_control(rig, record=journal.record)
        control.action = action
        assert control.run_once(write_guard=nullcontext()).state == "REQUESTED"
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()) == (1, 1)
        assert len([stage for stage in journal._stages(ledger._db) if stage.startswith("BG_CHAR_")]) == len(BMP_MARKER)
    with Ledger(path) as ledger:
        with pytest.raises(SocialError):
            BackgroundIntentJournal(SendState(ledger), operation, BINDING, action)
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"


def test_bmp_partial_text_stops_before_submit_without_clear_or_value_fallback():
    rig = Rig()
    # Simulate a native editor silently rejecting the final Chinese character.
    def after(message):
        if message == 0x102 and rig.body == BMP_MARKER:
            rig.body = rig.body[:-1]

    rig.after_post = after
    result = bmp_control(rig).run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN" and not result.submit_possible
    assert rig.body == BMP_MARKER[:-1]
    assert all(message not in {0x200, 0x201, 0x202} for message, _, _ in rig.posts)


def test_bmp_worker_selects_readonly_value_provider_and_exact_bmp_marker():
    session = StandaloneSession(BINDING.account.window, "a" * 64, allow_control=True,
                                input_method="utf16_bmp_messages")
    assert session.message_mode is True
    assert session.input_method == "utf16_bmp_messages"


def test_bmp_review_is_exact_and_remains_blocked_until_explicit_consumption(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        facts.pop("foreground_hit_and_focus")
        facts.pop("no_capture_or_hardware_input")
        facts.update(standalone_identity_verified=True, background_observer_active=True, no_capture=True)
        with pytest.raises(SocialError, match="scope_denied"):
            channel_review.authorize(ledger, binding, BMP_REQUEST, SOURCE_MARKER, facts,
                approval_ref="synthetic-bmp-approval", backend_kind="standalone_background_bmp")
        review = channel_review.authorize(ledger, binding, BMP_REQUEST, BMP_MARKER, facts,
            approval_ref="synthetic-bmp-approval", backend_kind="standalone_background_bmp")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        action = SendState(ledger).queue_marker(binding, BMP_REQUEST, BMP_MARKER, review_id=review)
        assert ledger.action_summary(action)["state"] == "QUEUED"


def test_source_uncertainty_stays_unchanged_when_named_for_a_future_bmp_trial(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, SOURCE_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        journal.record("VALUE_INPUT_INTENT")
        state.finish(operation, reaped=True, restored=True, reason="background_target_became_foreground")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic")
        assert len(channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))) == 64
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
        ledger._db.execute("UPDATE wechat_send_operations SET submit_permitted=1")
        with pytest.raises(SocialError):
            channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))
