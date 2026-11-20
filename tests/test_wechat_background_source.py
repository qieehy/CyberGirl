"""Input-only observation scope: no real WeChat or clipboard access."""

import base64
import hashlib
import io
import json
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.wechat_background_control import StandaloneSourceDiagnostic
from scripts.wechat_standalone_backend import StandaloneCapabilities
from scripts.wechat_standalone_worker import StandaloneSession
from tests.test_wechat_background_control import ACTION, BINDING, Rig
from tests.test_wechat_background_journal import prepare
from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_contract import (
    DEFERRED_MARKER,
    SOURCE_MARKER,
    SOURCE_REQUEST,
)
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def setup(*, fault=None, record=None):
    rig, calls = Rig(), []

    def value(token, text):
        assert token == rig.token and text == SOURCE_MARKER and not rig.body
        calls.append(text)
        rig.body = text.replace("🙂", "") if fault == "partial" else text
        if fault == "foreground":
            rig.guard = lambda: (_ for _ in ()).throw(SocialError("background_target_became_foreground"))
        if fault == "unknown":
            return False
        return True

    rig.set_value_once = value
    control = StandaloneSourceDiagnostic(BINDING, replace(ACTION, text=SOURCE_MARKER),
        observe=rig.observe, guard=lambda: rig.guard(), transport=rig, record=record or rig.record,
        clock=lambda: rig.now, sleep=rig.sleep)
    return control, rig, calls


@pytest.mark.parametrize("fault", [None, "partial", "unknown", "foreground", "permit"])
def test_diagnostic_never_focuses_clicks_retries_or_claims_delivery(fault):
    control, rig, calls = setup(fault=fault)
    if fault == "permit":
        rig.fail_record = "VALUE_INPUT_INTENT"
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "UNKNOWN"
    assert calls == ([] if fault == "permit" else [SOURCE_MARKER])
    assert result.posts_attempted == 0 and not rig.posts
    assert not result.submit_possible and not result.delivery_verified
    assert rig.intents == ["VALUE_INPUT_INTENT"]
    assert (result.reason == "background_source_diagnostic_complete") == (fault is None)
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())
    with pytest.raises(SocialError, match="source_effect_denied"):
        control._post(0x201, 1, 0)


@pytest.mark.parametrize("stage", ["PSEUDO_FOCUS_INTENT", "CHAR_INTENT", "MOVE_INTENT", "DOWN_INTENT",
                                    "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"])
def test_diagnostic_original_ledger_rejects_every_non_value_effect(tmp_path, stage):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, SOURCE_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        control, rig, calls = setup(record=journal.record)
        control.action = action
        assert control.run_once(write_guard=nullcontext()).reason == "background_source_diagnostic_complete"
        assert calls == [SOURCE_MARKER] and not rig.posts
        with pytest.raises(SocialError, match="source_effect_denied"):
            journal.record(stage)
        with pytest.raises(SocialError, match="source_submission_denied"):
            state.permit(operation, BINDING, action, "SUBMIT_PERMITTED")
        assert journal._stages(ledger._db) == ["BG_VALUE_INPUT_INTENT"]
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()) == (1, 0)


def test_diagnostic_transport_denies_messages_before_native_or_observer_access():
    capability = StandaloneCapabilities.__new__(StandaloneCapabilities)
    capability.pipe = SimpleNamespace(expected_marker=SOURCE_MARKER)
    for message in (7, 8, 0x102, 0x200, 0x201, 0x202):
        with pytest.raises(SocialError, match="source_effect_denied"):
            capability.post(message, 0, 0)


@pytest.mark.parametrize("allow", [False, True])
def test_diagnostic_worker_is_readonly_unless_explicit_and_exact_marker_bound(allow):
    session = StandaloneSession(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                allow_control=allow, input_method="uia_source_diagnostic")
    action = replace(ACTION, text=SOURCE_MARKER)
    session.handle(dict(version=1, operation_id="source", sequence=1, command="init",
                        binding=asdict(BINDING), action=asdict(action)), io.BytesIO(), io.BytesIO())
    if not allow:
        session.capabilities = object()
        with pytest.raises(SocialError, match="not_permitted"):
            session.handle(dict(version=1, operation_id="source", sequence=2, command="run_background"),
                           io.BytesIO(), io.BytesIO())
    session = StandaloneSession(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                input_method="uia_source_diagnostic")
    with pytest.raises(SocialError, match="marker_method_mismatch"):
        session.handle(dict(version=1, operation_id="source", sequence=1, command="init",
                            binding=asdict(BINDING), action=asdict(ACTION)), io.BytesIO(), io.BytesIO())


def test_diagnostic_fixed_receiver_base64_matches_complete_marker():
    source = Path("scripts/wechat_standalone_surface.ps1").read_text(encoding="utf-8")
    assert base64.b64encode(SOURCE_MARKER.encode()).decode() in source


def test_explicit_worker_dispatches_only_one_durable_value_intent_and_keeps_draft():
    session = StandaloneSession(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                allow_control=True, input_method="uia_source_diagnostic")
    action = replace(ACTION, text=SOURCE_MARKER)
    session.handle(dict(version=1, operation_id="source", sequence=1, command="init",
                        binding=asdict(BINDING), action=asdict(action)), io.BytesIO(), io.BytesIO())
    rig = Rig()

    def value(token, text):
        assert token == rig.token and text == SOURCE_MARKER and not rig.body
        rig.body = text
        return True

    rig.set_value_once = value
    session.capabilities = rig
    ack = dict(operation_id="source", sequence=2,
               background_ack=dict(stage="VALUE_INPUT_INTENT", ordinal=1, accepted=True))
    source, destination = io.BytesIO(json.dumps(ack).encode() + b"\n"), io.BytesIO()
    request = dict(version=1, operation_id="source", sequence=2, command="run_background")
    result = session.handle(request, source, destination)["result"]
    assert result["control_result"]["reason"] == "background_source_diagnostic_complete"
    assert result["control_result"]["submit_possible"] is False
    assert rig.body == SOURCE_MARKER and not rig.posts
    frames = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert len(frames) == 1 and frames[0]["background_intent"]["stage"] == "VALUE_INPUT_INTENT"
    with pytest.raises(SocialError):
        session.handle({**request, "sequence": 3}, io.BytesIO(), io.BytesIO())


def test_source_input_crash_recovers_without_a_new_permit_or_replay(tmp_path):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        state, operation, action = prepare(ledger, SOURCE_MARKER)
        BackgroundIntentJournal(state, operation, BINDING, action).record("VALUE_INPUT_INTENT")
    with Ledger(path) as ledger:
        assert tuple(ledger._db.execute("SELECT input_permitted,submit_permitted,finished FROM wechat_send_operations").fetchone()) == (1, 0, 1)
        with pytest.raises(SocialError):
            BackgroundIntentJournal(SendState(ledger), operation, BINDING, action)
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"


def test_diagnostic_review_requires_exact_new_marker_and_does_not_unblock_before_consumption(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        facts.pop("foreground_hit_and_focus")
        facts.pop("no_capture_or_hardware_input")
        facts.update(standalone_identity_verified=True, background_observer_active=True, no_capture=True)
        with pytest.raises(SocialError, match="scope_denied"):
            channel_review.authorize(ledger, binding, SOURCE_REQUEST, DEFERRED_MARKER, facts,
                approval_ref="synthetic-source-approval", backend_kind="standalone_background_source")
        review = channel_review.authorize(ledger, binding, SOURCE_REQUEST, SOURCE_MARKER, facts,
            approval_ref="synthetic-source-approval", backend_kind="standalone_background_source")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        action = SendState(ledger).queue_marker(binding, SOURCE_REQUEST, SOURCE_MARKER, review_id=review)
        assert ledger.action_summary(action)["state"] == "QUEUED"


@pytest.mark.parametrize("damage", [None, "submit", "focus", "reason"])
def test_named_deferred_uncertainty_exception_remains_input_only_and_exact(tmp_path, damage):
    with Ledger(tmp_path / "ledger.db") as ledger:
        state, operation, action = prepare(ledger, DEFERRED_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        journal.record("VALUE_INPUT_INTENT")
        if damage == "focus":
            journal.record("PSEUDO_FOCUS_INTENT")
        state.finish(operation, reaped=True, restored=True, reason=(
            "other" if damage == "reason" else "background_target_became_foreground"))
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic")
        if damage == "submit":
            ledger._db.execute("UPDATE wechat_send_operations SET submit_permitted=1")
        if damage:
            with pytest.raises(SocialError, match="uncertainty_source_invalid"):
                channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))
        else:
            assert len(channel_review.history(ledger._db, BINDING.scope.key(), reviewed_unknowns=(action.action_id,))) == 64
            assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
