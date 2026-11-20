"""Explicit acceptance of a named UNKNOWN, never a delivery confirmation."""

import json
from dataclasses import replace

import pytest

from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.directed_native import TRIAL_MARKER
from wechat_agent.bridges.wechat.posted_native import POSTED_TRIAL_MARKER
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def unknown_prior(ledger):
    binding, facts = prior(ledger)
    state = SendState(ledger)
    review = channel_review.authorize(ledger, binding, "directed-once", TRIAL_MARKER, facts, approval_ref="prior-trial-approval")
    action_id = state.queue_marker(binding, "directed-once", TRIAL_MARKER, review_id=review)
    action = ledger.claim(action_id)
    operation = state.begin(binding, action, {"selected": Cursor(0, 0)})
    state.permit(operation, binding, action, "INPUT_PERMITTED")
    state.permit(operation, binding, action, "SUBMIT_PERMITTED")
    state.finish(operation, reaped=True, restored=True, reason="unknown_button_state")
    ledger.finish_delivery(action_id, "UNCONFIRMED", "unknown_button_state")
    fresh = replace(binding, account=replace(binding.account, window=replace(binding.account.window,
        pid=binding.account.window.pid + 1, created_filetime=binding.account.window.created_filetime + 1)))
    BridgeStore(ledger).bind(fresh, mode="draft")
    return fresh, {**facts, "binding_generation": fresh.generation}, action_id, operation


def approve(ledger, binding, facts, unknown, **kwargs):
    return channel_review.authorize(ledger, binding, "posted-once", POSTED_TRIAL_MARKER, facts,
        approval_ref="new-single-trial-approval", reviewed_unknowns=(unknown,),
        uncertainty_approval_ref="explicit-review-of-prior-unknown", **kwargs)


def test_named_unknown_review_is_a_separate_fact_and_does_not_confirm_unblock_or_replay_it(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts, unknown, _ = unknown_prior(ledger)
        state = SendState(ledger)
        with pytest.raises(SocialError, match="unreconciled"):
            channel_review.authorize(ledger, binding, "posted-once", POSTED_TRIAL_MARKER, facts, approval_ref="new-trial-approval-alone")
        review = approve(ledger, binding, facts, unknown)
        row = ledger._db.execute("SELECT * FROM wechat_channel_reviews WHERE review_id=?", (review,)).fetchone()
        assert json.loads(row["facts"])["prior_uncertainty_review"] == {
            "approval_ref": "explicit-review-of-prior-unknown", "action_ids": [unknown]}
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        with pytest.raises(SocialError):
            state.queue_marker(binding, "unreviewed", POSTED_TRIAL_MARKER)
        action_id = state.queue_marker(binding, "posted-once", POSTED_TRIAL_MARKER, review_id=review)
        assert state.queue_marker(binding, "posted-once", POSTED_TRIAL_MARKER, review_id=review) == action_id
        action = ledger.claim(action_id)
        operation = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.finish(operation, reaped=True, restored=True, reason="synthetic_before_input_stop")
        ledger.finish_delivery(action_id, "FAILED", "synthetic_before_input_stop")
        assert ledger.action_summary(unknown)["state"] == "UNCONFIRMED" and ledger.claim(unknown) is None
        assert ledger._db.execute("SELECT state FROM attempts WHERE action_id=?", (unknown,)).fetchone()[0] == "UNCONFIRMED"
        assert not ledger._db.execute("SELECT 1 FROM delivery_evidence WHERE action_id=?", (unknown,)).fetchone()
        assert ledger._db.execute("SELECT COUNT(*) FROM delivery_evidence WHERE kind='manual_confirmation'").fetchone()[0] == 1
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]


@pytest.mark.parametrize("failure", ["same_process", "process_still_alive", "draft_present", "cleanup_unknown", "attempt_not_unknown", "history_changed", "old_marker"])
def test_uncertainty_acceptance_cannot_replace_live_fences_cleanup_or_new_body(tmp_path, failure):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts, unknown, operation = unknown_prior(ledger)
        marker = POSTED_TRIAL_MARKER
        if failure == "same_process":
            old = json.loads(ledger._db.execute("SELECT window_binding FROM wechat_send_operations WHERE action_id=?", (unknown,)).fetchone()[0])
            binding = replace(binding, account=replace(binding.account, window=type(binding.account.window)(**old)))
            BridgeStore(ledger).bind(binding, mode="draft")
            facts["binding_generation"] = binding.generation
        elif failure == "process_still_alive":
            facts["prior_process_generation_exited"] = False
        elif failure == "draft_present":
            facts["both_draft_patterns_empty"] = False
        elif failure == "cleanup_unknown":
            ledger._db.execute("UPDATE wechat_send_operations SET worker_reaped=0 WHERE operation_id=?", (operation,))
        elif failure == "attempt_not_unknown":
            ledger._db.execute("UPDATE attempts SET state='FAILED' WHERE action_id=?", (unknown,))
        elif failure == "history_changed":
            review = approve(ledger, binding, facts, unknown)
            ledger._db.execute("UPDATE wechat_send_operations SET reason='changed' WHERE operation_id=?", (operation,))
            with pytest.raises(SocialError):
                SendState(ledger).queue_marker(binding, "posted-once", marker, review_id=review)
            assert ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2
            return
        else:
            marker = TRIAL_MARKER
        with pytest.raises(SocialError):
            channel_review.authorize(ledger, binding, "posted-once", marker, facts, approval_ref="new-single-trial-approval",
                reviewed_unknowns=(unknown,), uncertainty_approval_ref="explicit-review-of-prior-unknown")
        assert ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2


@pytest.mark.parametrize("actions,reference", [((), "reference"), (("a" * 64,), None),
    (("a" * 64, "a" * 64), "reference"), ((["invalid"],), "reference"), (("missing-action",), "reference")])
def test_review_requires_exact_valid_named_unknown_actions_and_explicit_reference(tmp_path, actions, reference):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts, _, _ = unknown_prior(ledger)
        with pytest.raises(SocialError):
            channel_review.authorize(ledger, binding, "posted-once", POSTED_TRIAL_MARKER, facts,
                approval_ref="new-trial-approval", reviewed_unknowns=actions, uncertainty_approval_ref=reference)


def test_acceptance_of_one_unknown_does_not_accept_a_later_unknown(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts, unknown, _ = unknown_prior(ledger)
        review = approve(ledger, binding, facts, unknown)
        state = SendState(ledger)
        action_id = state.queue_marker(binding, "posted-once", POSTED_TRIAL_MARKER, review_id=review)
        action = ledger.claim(action_id)
        op = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.permit(op, binding, action, "INPUT_PERMITTED")
        state.permit(op, binding, action, "SUBMIT_PERMITTED")
        state.finish(op, reaped=True, restored=True, reason="another_unknown")
        ledger.finish_delivery(action_id, "UNCONFIRMED", "another_unknown")
        newer = replace(binding, account=replace(binding.account, window=replace(binding.account.window,
            pid=binding.account.window.pid + 1, created_filetime=binding.account.window.created_filetime + 1)))
        BridgeStore(ledger).bind(newer, mode="draft")
        with pytest.raises(SocialError, match="unreconciled"):
            channel_review.authorize(ledger, newer, "other", "W2B-unapproved-other", {**facts, "binding_generation": newer.generation},
                approval_ref="other-trial-ref", reviewed_unknowns=(unknown,), uncertainty_approval_ref="old-unknown-review")
