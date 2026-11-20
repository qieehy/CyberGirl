"""Read-only settling and delivery reconciliation never repeat UI effects."""

import hashlib
from types import SimpleNamespace

import pytest

from scripts import probe_wechat_background_trial as host
from scripts import wechat_standalone_backend as backend
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_background_journal import prepare
from wechat_agent.bridges.wechat.background_contract import RETURN_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.contracts import DeliveryEvidence
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
def test_only_fresh_native_reads_are_repeated_with_a_three_attempt_bound(monkeypatch, failures):
    calls = []

    def verify(*args, **kwargs):
        calls.append((args[1], kwargs["expected_fingerprint"]))
        if len(calls) <= failures:
            raise SocialError("native_object_changed")
        return {"stable": True}

    monkeypatch.setattr(backend, "verify_standalone_filehelper", verify)
    monkeypatch.setattr(backend.time, "sleep", lambda delay: None)
    capability = backend.StandaloneCapabilities.__new__(backend.StandaloneCapabilities)
    capability.surface = SimpleNamespace(memory=object(), account_reader=lambda: "fixture", fingerprint="a" * 64)
    capability.ids = {"chat_input": [3, 0x80000001]}
    capability.observer = SimpleNamespace(check=lambda: True)
    if failures == 3:
        with pytest.raises(SocialError, match="native_object_changed"):
            capability._native()
    else:
        assert capability._native() == {"stable": True}
    assert len(calls) == min(failures + 1, 3)
    assert all(item == ([3, 0x80000001], "a" * 64) for item in calls)


@pytest.mark.parametrize("reason", ["standalone_account_mismatch", "standalone_page_not_filehelper", "native_object_pointer_invalid"])
def test_real_identity_or_layout_rejection_is_never_retried(monkeypatch, reason):
    calls = []

    def verify(*_args, **_kwargs):
        calls.append(1)
        raise SocialError(reason)

    monkeypatch.setattr(backend, "verify_standalone_filehelper", verify)
    capability = backend.StandaloneCapabilities.__new__(backend.StandaloneCapabilities)
    capability.surface = SimpleNamespace(memory=object(), account_reader=lambda: "fixture", fingerprint="a" * 64)
    capability.ids = {"chat_input": [3, 0x80000001]}
    capability.observer = SimpleNamespace(check=lambda: True)
    with pytest.raises(SocialError, match=reason):
        capability._native()
    assert calls == [1]


def test_handset_delivery_can_be_confirmed_without_reclassifying_failed_background_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        state, operation, action = prepare(ledger, RETURN_MARKER)
        journal = BackgroundIntentJournal(state, operation, BINDING, action)
        for stage in ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * len(RETURN_MARKER) + ["RETURN_DOWN_INTENT", "RETURN_UP_INTENT"]:
            journal.record(stage)
        state.finish(operation, reaped=True, restored=True, reason="native_object_changed")
        ledger.finish_delivery(action.action_id, "UNCONFIRMED", "native_object_changed")
    prior = dict(mode="standalone_background_return", action_id=action.action_id, operation_id=operation,
                 worker_reaped=True, reader_reaped=True, provider_restored=True,
                 worker_local_cleanup_verified=True, unique_local_echo=True, locks_released=True,
                 submit_permitted=1, control_result={"focus_cleanup_verified": False})
    monkeypatch.setattr(host, "LEDGER", path)
    with pytest.raises(SocialError, match="phone_evidence_incomplete"):
        host.confirm_phone(prior, {})
    with pytest.raises(SocialError, match="phone_local_echo_required"):
        host.confirm_phone(prior, {}, delivery_only=True)
    with Ledger(path) as ledger:
        BridgeStore(ledger).record_evidence(DeliveryEvidence("local-proof", action.action_id, BINDING.scope,
            BINDING.generation, "local_echo", operation, hashlib.sha256(RETURN_MARKER.encode()).hexdigest(), "native-message"))
    output = {}
    host.confirm_phone(prior, output, delivery_only=True)
    assert output["delivery_state"] == "CONFIRMED" and output["background_acceptance"] is False
    assert output["confirmation_scope"] == "delivery_only"
    with Ledger(path) as ledger:
        assert ledger._db.execute("SELECT reason FROM wechat_send_operations").fetchone()[0] == "native_object_changed"
        assert ledger._db.execute("SELECT state FROM attempts").fetchone()[0] == "UNCONFIRMED"


@pytest.mark.parametrize("states,expected", [([True, False], True), ([True, True, False], True), ([True] * 3, False)])
def test_cleanup_only_rereads_focus_until_a_bounded_match_and_never_reposts(monkeypatch, states, expected):
    monkeypatch.setattr(backend.time, "sleep", lambda delay: None)
    capability = backend.StandaloneCapabilities.__new__(backend.StandaloneCapabilities)
    capability.pseudo_focus_owned, capability.initial_qt_focus = True, True
    capability.guard = lambda: True
    calls = []

    def observe():
        capability.last_snapshot = {"qt_has_keyboard_focus": states[len(calls)]}
        calls.append(1)

    capability.observe = observe
    assert capability.verify_cleanup() is expected
    assert len(calls) == len(states)
