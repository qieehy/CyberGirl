import copy
import hashlib
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from scripts.wechat_background_control import ControlResult
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_background_journal import prepare
from wechat_agent.bridges.wechat.background_completion import (
    CLEAN_STAGE,
    clean_background_result,
    record_background_completion,
    require_background_admission,
)
from wechat_agent.bridges.wechat.background_contract import COVERED_MARKER
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.contracts import DeliveryEvidence
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def clean_result(text=COVERED_MARKER):
    observer = BackgroundObserver(123)
    observer.thread = SimpleNamespace(is_alive=lambda: False)
    observer.samples, observer.max_gap, observer.unhooked = 10, 0.03, True
    return dict(control_result=asdict(ControlResult("REQUESTED", "sent", True, True, True,
                len(text), len(text) + 4, True)), worker_local_cleanup_verified=True, observation=observer.report())


def finished(ledger):
    state, operation, action = prepare(ledger, COVERED_MARKER)
    journal = BackgroundIntentJournal(state, operation, BINDING, action)
    for stage in ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * len(COVERED_MARKER) + ["RETURN_DOWN_INTENT", "RETURN_UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]:
        journal.record(stage)
    state.finish(operation, reaped=True, restored=True, reason="background_request_awaiting_echo")
    ledger.finish_delivery(action.action_id, "UNCONFIRMED", "synthetic")
    return operation, action


@pytest.mark.parametrize("damage", ["foreground", "focus", "gap", "unhooked", "posts", "unverified_delivery", "extra"])
def test_clean_control_fact_rejects_incomplete_or_overclaimed_evidence(damage):
    proof = copy.deepcopy(clean_result())
    if damage == "foreground":
        proof["observation"]["foreground_target_seen"] = True
    elif damage == "focus":
        proof["control_result"]["focus_cleanup_verified"] = False
    elif damage == "gap":
        proof["observation"]["maximum_sample_gap_ms"] = 251
    elif damage == "unhooked":
        proof["observation"]["hook_released"] = False
    elif damage == "posts":
        proof["control_result"]["posts_attempted"] -= 1
    elif damage == "unverified_delivery":
        proof["control_result"]["delivery_verified"] = True
    else:
        proof["observation"]["text"] = "must not leak UI content"
    assert not clean_background_result(proof, COVERED_MARKER)


def test_completion_is_idempotent_but_never_confirms_delivery_or_unblocks_channel(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        operation, action = finished(ledger)
        assert record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
        assert not record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_journal WHERE stage=?", (CLEAN_STAGE,)).fetchone()[0] == 1
        with pytest.raises(SocialError, match="unreconciled"):
            require_background_admission(ledger, BINDING)


def test_generation_admission_requires_clean_control_unique_echo_and_manual_confirmation(tmp_path):
    with Ledger(tmp_path / "ledger.db") as ledger:
        operation, action = finished(ledger)
        record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
        sha = hashlib.sha256(COVERED_MARKER.encode()).hexdigest()
        store = BridgeStore(ledger)
        store.record_evidence(DeliveryEvidence("echo", action.action_id, BINDING.scope, BINDING.generation,
                                               "local_echo", operation, sha, "native-echo"))
        with pytest.raises(SocialError):
            require_background_admission(ledger, BINDING)
        store.record_evidence(DeliveryEvidence("manual", action.action_id, BINDING.scope, BINDING.generation,
                                               "manual_confirmation", "user-phone", sha))
        assert require_background_admission(ledger, BINDING)
        ledger._db.execute("UPDATE wechat_send_operations SET restore_verified=0")
        with pytest.raises(SocialError, match="unreconciled"):
            require_background_admission(ledger, BINDING)


@pytest.mark.parametrize("damage", ["extra", "bool_count", "nan", "reason", "text_field"])
def test_child_diagnostics_reject_unbounded_or_arbitrary_payload(damage):
    from wechat_agent.bridges.wechat.background_completion import project_background_result
    result = clean_result()
    if damage == "extra":
        result["raw_text"] = "private"
    elif damage == "bool_count":
        result["control_result"]["posts_attempted"] = True
    elif damage == "nan":
        result["observation"]["maximum_sample_gap_ms"] = float("nan")
    elif damage == "reason":
        result["control_result"]["reason"] = "arbitrary UI content"
    else:
        result["observation"]["text"] = "private"
    with pytest.raises(SocialError, match="result_invalid"):
        project_background_result(result)


def test_admitted_review_uses_current_clean_generation_without_faking_process_exit(tmp_path):
    from wechat_agent.bridges.wechat import channel_review
    from wechat_agent.bridges.wechat.background_contract import (
        INTEGRATED_MARKER,
        INTEGRATED_REQUEST,
    )
    from wechat_agent.bridges.wechat.send_state import SendState
    with Ledger(tmp_path / "ledger.db") as ledger:
        operation, action = finished(ledger)
        record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
        store = BridgeStore(ledger)
        sha = hashlib.sha256(COVERED_MARKER.encode()).hexdigest()
        store.record_evidence(DeliveryEvidence("echo", action.action_id, BINDING.scope, BINDING.generation,
                                               "local_echo", operation, sha, "native-echo"))
        store.record_evidence(DeliveryEvidence("manual", action.action_id, BINDING.scope, BINDING.generation,
                                               "manual_confirmation", "user-phone", sha))
        facts = {key: True for key in {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                "button_not_pressed", "class_entry_matches_pinned_code", "standalone_identity_verified",
                "background_observer_active", "no_capture", "covered_by_other_app",
                "prior_input_context_reconciled", "background_admission_verified"}}
        facts.update(observed_at=ledger.now(), binding_generation=BINDING.generation)
        review = channel_review.authorize(ledger, BINDING, INTEGRATED_REQUEST, INTEGRATED_MARKER, facts,
            approval_ref="bounded-integration", backend_kind="integrated_background_bmp")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        # Revoking any original proof invalidates an unconsumed review.
        ledger._db.execute("DELETE FROM wechat_send_journal WHERE stage=?", (CLEAN_STAGE,))
        with pytest.raises(SocialError, match="unreconciled"):
            SendState(ledger).queue_marker(BINDING, INTEGRATED_REQUEST, INTEGRATED_MARKER, review_id=review)
        assert ledger._db.execute("SELECT action_id FROM wechat_channel_reviews WHERE review_id=?", (review,)).fetchone()[0] is None
