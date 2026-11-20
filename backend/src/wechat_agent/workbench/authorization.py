"""One concrete, operator-confirmed workbench action in the original ledger."""

import hashlib
import json

from ..bridges.wechat import channel_review
from ..bridges.wechat.background_readmission import READY_FIELDS
from ..bridges.wechat.background_text import require_background_bmp_text
from ..bridges.wechat.contracts import digest
from ..social.models import SocialError

KIND = "workbench_background_bmp"


def authorized(db, action_id, text):
    row = db.execute("""SELECT d.draft_id,d.state,r.facts,r.marker_sha256 FROM workbench_drafts d
        JOIN wechat_channel_reviews r USING(action_id) WHERE d.action_id=?""", (action_id,)).fetchone()
    if row is None or row["state"] != "SUBMITTED" or row["marker_sha256"] != hashlib.sha256(text.encode()).hexdigest():
        return False
    facts = json.loads(row["facts"])
    return facts.get("backend_kind") == KIND and facts.get("draft_id") == row["draft_id"]


def create_review(db, ledger, binding, draft, revision, request_id, facts, checker, *, reviewed_unknowns=(), uncertainty_reference=None):
    require_background_bmp_text(revision["text"])
    if type(facts) is not dict or set(facts) != READY_FIELDS or any(v is not True for v in facts.values()):
        raise SocialError("background_preparation_incomplete")
    if checker(ledger, binding) is not True:
        raise SocialError("background_admission_required")
    if db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] not in {
            "submission_or_draft_requires_review", "reviewed_trial_requires_review"}:
        raise SocialError("channel_review_block_changed")
    reviewed = channel_review._reviewed_actions(tuple(reviewed_unknowns), uncertainty_reference)
    prior_hash = channel_review.history(db, binding.scope.key(), reviewed_unknowns=reviewed)
    if db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0").fetchone() or db.execute(
            "SELECT 1 FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchone():
        raise SocialError("ui_operation_busy")
    stored = dict(facts, backend_kind=KIND, draft_id=draft["draft_id"], draft_version=revision["version"],
                  message_revision=draft["message_revision"], persona_version=draft["persona_version"],
                  policy_revision=draft["policy_revision"])
    if reviewed:
        stored["prior_uncertainty_review"] = dict(approval_ref=uncertainty_reference, action_ids=list(reviewed))
    rid = digest([binding.generation, request_id, revision["sha256"], stored])
    now = ledger.now()
    db.execute("INSERT INTO wechat_channel_reviews VALUES(?,?,?,?,?,?,?,?,?,?,NULL,0)",
               (rid, binding.scope.key(), binding.generation, request_id, revision["sha256"], prior_hash,
                json.dumps(stored, sort_keys=True), "workbench:" + request_id, now, min(now + 60, draft["expires_at"])))
    return rid


def consume_review(db, ledger, binding, review_id, action_id, checker):
    if (db.execute("SELECT 1 FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchone()
            or db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0").fetchone()):
        raise SocialError("ui_operation_busy")
    if db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] not in {
            "submission_or_draft_requires_review", "reviewed_trial_requires_review"}:
        raise SocialError("channel_review_block_changed")
    row = db.execute("SELECT * FROM wechat_channel_reviews WHERE review_id=?", (review_id,)).fetchone()
    if (not row or row["action_id"] or row["finished"] or row["generation"] != binding.generation
            or not row["approved_at"] <= ledger.now() < row["expires_at"]):
        raise SocialError("channel_review_invalid_expired_or_consumed")
    facts = json.loads(row["facts"])
    accepted = facts.get("prior_uncertainty_review", {})
    reviewed = channel_review._reviewed_actions(tuple(accepted.get("action_ids", ())), accepted.get("approval_ref"))
    if facts.get("backend_kind") != KIND or row["history_sha256"] != channel_review.history(db, binding.scope.key(), reviewed_unknowns=reviewed):
        raise SocialError("channel_review_block_changed")
    if checker(ledger, binding) is not True:
        raise SocialError("background_admission_required")
    db.execute("UPDATE wechat_channel_reviews SET action_id=? WHERE review_id=?", (action_id, review_id))
    db.execute("UPDATE wechat_send_control SET blocked_reason=NULL")
