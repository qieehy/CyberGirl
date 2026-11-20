"""Independent one-use review, history preservation and migration recovery."""

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.contracts import Cursor, DeliveryEvidence
from wechat_agent.bridges.wechat.directed_native import TRIAL_MARKER
from wechat_agent.bridges.wechat.posted_native import POSTED_TRIAL_MARKER
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError

NOW = 1_800_000_000


def prior(ledger):
    store, state = BridgeStore(ledger), SendState(ledger)
    store.bind(BINDING, mode="draft")
    ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
    action_id = state.queue_marker(BINDING, "historical-fixed", "W2B-historical")
    action = ledger.claim(action_id)
    op = state.begin(BINDING, action, {"selected": Cursor(0, 0)})
    state.permit(op, BINDING, action, "INPUT_PERMITTED")
    state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
    state.finish(op, reaped=True, restored=True, reason="historical_unknown")
    ledger.finish_delivery(action_id, "UNCONFIRMED", "historical_unknown")
    store.record_evidence(DeliveryEvidence("prior-phone-evidence", action_id, BINDING.scope,
        BINDING.generation, "manual_confirmation", "user-phone-checked",
        hashlib.sha256(action.text.encode()).hexdigest()))
    binding = replace(BINDING, account=replace(BINDING.account, window=replace(BINDING.account.window,
        pid=BINDING.account.window.pid + 1, created_filetime=BINDING.account.window.created_filetime + 1)))
    store.bind(binding, mode="draft")
    facts = dict(account_and_filehelper_verified=True, both_draft_patterns_empty=True, same_editor_and_button=True,
        foreground_hit_and_focus=True, no_capture_or_hardware_input=True, button_not_pressed=True,
        class_entry_matches_pinned_code=True, prior_process_generation_exited=True,
        observed_at=NOW, binding_generation=binding.generation)
    return binding, facts


def test_review_is_not_an_unblock_and_only_the_exact_new_trial_can_consume_it(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        state = SendState(ledger)
        review = channel_review.authorize(ledger, binding, "new-one-shot", TRIAL_MARKER, facts, approval_ref="operator-specific-trial")
        with pytest.raises(SocialError, match="requires_review"):
            state.queue_marker(binding, "unreviewed", TRIAL_MARKER)
        with pytest.raises(SocialError):
            state.queue_marker(binding, "different-request", TRIAL_MARKER, review_id=review)
        action_id = state.queue_marker(binding, "new-one-shot", TRIAL_MARKER, review_id=review)
        with pytest.raises(SocialError):
            state.queue_marker(binding, "another", "W2B-another")
        action = ledger.claim(action_id)
        op = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.finish(op, reaped=True, restored=True, reason="stopped_before_input")
        ledger.finish_delivery(action_id, "FAILED", "stopped_before_input")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "reviewed_trial_requires_review"
        assert ledger._db.execute("SELECT state FROM outbox WHERE action_id!=?", (action_id,)).fetchone()[0] == "CONFIRMED"


@pytest.mark.parametrize("failure", ["stale", "wrong_generation", "prior_process_live", "old_marker", "history_changed"])
def test_incomplete_review_or_changed_history_cannot_admit_a_trial(tmp_path, failure):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        marker = TRIAL_MARKER
        if failure == "stale":
            facts["observed_at"] -= 21
        elif failure == "wrong_generation":
            facts["binding_generation"] = "other"
        elif failure == "prior_process_live":
            facts["prior_process_generation_exited"] = False
        elif failure == "old_marker":
            marker = "W2B-historical"
        else:
            review = channel_review.authorize(ledger, binding, "new-one-shot", marker, facts, approval_ref="operator-specific-trial")
            ledger._db.execute("UPDATE wechat_send_operations SET restore_verified=0")
            with pytest.raises(SocialError):
                SendState(ledger).queue_marker(binding, "new-one-shot", marker, review_id=review)
            return
        with pytest.raises(SocialError):
            channel_review.authorize(ledger, binding, "new-one-shot", marker, facts, approval_ref="operator-specific-trial")


def test_crash_after_consumption_before_begin_cancels_instead_of_replaying(tmp_path):
    path = tmp_path / "ledger.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        review = channel_review.authorize(ledger, binding, "new-one-shot", TRIAL_MARKER, facts, approval_ref="operator-specific-trial")
        action_id = SendState(ledger).queue_marker(binding, "new-one-shot", TRIAL_MARKER, review_id=review)
    with Ledger(path, now=lambda: NOW) as ledger:
        assert ledger.action_summary(action_id)["state"] == "CANCELLED"
        assert ledger.claim(action_id) is None
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "interrupted_reviewed_trial"


def test_unknown_input_trial_cannot_repeat_or_be_bypassed_by_another_process_generation(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        state = SendState(ledger)
        review = channel_review.authorize(ledger, binding, "one-trial", TRIAL_MARKER, facts, approval_ref="operator-specific-trial")
        action_id = state.queue_marker(binding, "one-trial", TRIAL_MARKER, review_id=review)
        action = ledger.claim(action_id)
        op = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.permit(op, binding, action, "INPUT_PERMITTED")
        state.permit(op, binding, action, "SUBMIT_PERMITTED")
        state.finish(op, reaped=True, restored=True, reason="press_not_observed")
        ledger.finish_delivery(action_id, "UNCONFIRMED", "press_not_observed")
        assert state.queue_marker(binding, "one-trial", TRIAL_MARKER, review_id=review) == action_id
        assert ledger.claim(action_id) is None
        new_window = replace(binding.account.window, pid=binding.account.window.pid + 1,
                             created_filetime=binding.account.window.created_filetime + 1)
        changed = replace(binding, account=replace(binding.account, window=new_window))
        BridgeStore(ledger).bind(changed, mode="draft")
        with pytest.raises(SocialError, match="prior_delivery_unreconciled"):
            channel_review.authorize(ledger, changed, "another-trial", "W2B-another-marker",
                {**facts, "binding_generation": changed.generation}, approval_ref="another-approval")
        assert ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_channel_reviews").fetchone()[0] == 1


def test_v7_backup_and_migration_failure_preserve_historical_confirmation(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        prior(ledger)
        for name in ("reader_private_targets", "workbench_requests", "workbench_draft_revisions", "workbench_drafts", "workbench_context", "bridge_message_payloads", "wechat_channel_reviews"):
            ledger._db.execute(f"DROP TABLE {name}")
        ledger._db.execute("PRAGMA user_version=7")
    original = channel_review.migrate

    def fail(db):
        original(db)
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(channel_review, "migrate", fail)
    with pytest.raises(RuntimeError), Ledger(path):
        pass
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_channel_reviews'").fetchone()
        assert db.execute("SELECT state FROM outbox").fetchone()[0] == "CONFIRMED"
    monkeypatch.setattr(channel_review, "migrate", original)
    with Ledger(path) as ledger:
        assert ledger._db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert ledger._db.execute("SELECT state FROM outbox").fetchone()[0] == "CONFIRMED"
    assert list(tmp_path.glob("ledger.db.v7-*.bak"))


@pytest.mark.parametrize("posted", [False, True])
def test_explicit_phone_confirmation_is_idempotent_and_cannot_reconcile_a_different_marker(tmp_path, monkeypatch, posted):
    from scripts import probe_wechat_directed_trial as trial

    path = tmp_path / "ledger.db"
    monkeypatch.setattr(trial, "LEDGER", path)
    marker = POSTED_TRIAL_MARKER if posted else TRIAL_MARKER
    with Ledger(path, now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        review = channel_review.authorize(ledger, binding, "new-one-shot", marker, facts, approval_ref="operator-specific-trial")
        state = SendState(ledger)
        action_id = state.queue_marker(binding, "new-one-shot", marker, review_id=review)
        action = ledger.claim(action_id)
        operation = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.permit(operation, binding, action, "INPUT_PERMITTED")
        state.permit(operation, binding, action, "SUBMIT_PERMITTED")
        state.finish(operation, reaped=True, restored=True, reason="synthetic_requested")
        ledger.finish_delivery(action_id, "UNCONFIRMED", "synthetic_requested")
    evidence = dict(mode="send_posted_reviewed_once" if posted else "send_reviewed_once", submit_permitted=1, worker_reaped=True, restore_verified=True,
                    reader_stopped=True, outer_restore_verified=True, action_id=action_id, operation_id=operation,
                    marker_sha256=hashlib.sha256(marker.encode()).hexdigest())
    evidence_path = tmp_path / "result.json"
    evidence_path.write_text(json.dumps({**evidence, "marker_sha256": "a" * 64}))
    with pytest.raises(SocialError, match="action_mismatch"):
        trial.confirm_phone(evidence_path, {})
    evidence_path.write_text(json.dumps({**evidence, "outer_restore_verified": False}))
    with pytest.raises(SocialError, match="evidence_incomplete"):
        trial.confirm_phone(evidence_path, {})
    evidence_path.write_text(json.dumps(evidence))
    with Ledger(path) as ledger:
        ledger._db.execute("UPDATE wechat_send_operations SET worker_reaped=0 WHERE action_id=?", (action_id,))
    with pytest.raises(SocialError, match="cleanup_unverified"):
        trial.confirm_phone(evidence_path, {})
    with Ledger(path) as ledger:
        ledger._db.execute("UPDATE wechat_send_operations SET worker_reaped=1 WHERE action_id=?", (action_id,))
    result = {}
    trial.confirm_phone(evidence_path, result)
    assert result["delivery_state"] == "CONFIRMED" and result["evidence_added"]
    assert result["marker_sha256"] == hashlib.sha256(marker.encode()).hexdigest()
    result = {}
    trial.confirm_phone(evidence_path, result)
    assert result["delivery_state"] == "CONFIRMED" and not result["evidence_added"]
    with Ledger(path) as ledger:
        assert ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2
        assert ledger._db.execute("SELECT state FROM attempts WHERE action_id=?", (action_id,)).fetchone()[0] == "UNCONFIRMED"
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
