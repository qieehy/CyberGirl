"""One operator-reviewed trial in the ORIGINAL ledger, never a general unblock."""

import json
import re

from ...social.models import SocialError, identifier, timestamp
from .contracts import digest


def migrate(db):
    db.execute("""CREATE TABLE wechat_channel_reviews(
        review_id TEXT PRIMARY KEY,scope TEXT NOT NULL,generation TEXT NOT NULL,
        request_id TEXT NOT NULL,marker_sha256 TEXT NOT NULL,history_sha256 TEXT NOT NULL,
        facts TEXT NOT NULL,approval_ref TEXT NOT NULL,approved_at REAL NOT NULL,
        expires_at REAL NOT NULL,action_id TEXT UNIQUE,finished INTEGER NOT NULL DEFAULT 0)""")
    db.execute("PRAGMA user_version=8")


def _reviewed_actions(actions, reference):
    if actions == () and reference is None:
        return ()
    if type(actions) is not tuple or not 1 <= len(actions) <= 10:
        raise SocialError("channel_review_uncertainty_scope_invalid")
    identifier(reference)
    for action in actions:
        identifier(action)
    if len(set(actions)) != len(actions):
        raise SocialError("channel_review_uncertainty_scope_invalid")
    return tuple(sorted(actions))


def history(db, scope, *, reviewed_unknowns=()):
    rows = db.execute("""SELECT s.*,o.state AS delivery_state FROM wechat_send_operations s
        JOIN outbox o USING(action_id) WHERE s.scope=? ORDER BY s.operation_id""", (scope,)).fetchall()
    if not rows or len(rows) > 100:
        raise SocialError("channel_review_history_unbounded_or_missing")
    manual = db.execute("SELECT action_id,kind,fingerprint FROM delivery_evidence ORDER BY evidence_id").fetchall()
    seen = set()
    for row in rows:
        if not row["finished"] or not row["worker_reaped"] or not row["restore_verified"]:
            raise SocialError("channel_review_prior_cleanup_unverified")
        if row["input_permitted"]:
            if row["action_id"] in reviewed_unknowns and row["delivery_state"] == "UNCONFIRMED":
                source = db.execute("""SELECT a.state,i.origin,o.text FROM attempts a JOIN outbox o USING(action_id)
                    JOIN inbox i USING(event_id) WHERE a.action_id=?""", (row["action_id"],)).fetchone()
                fixed_marker = bool(source and re.fullmatch(r"W2B-[A-Za-z0-9-]{1,64}", source["text"]))
                if source and not fixed_marker:
                    from .background_contract import (
                        BMP_MARKER,
                        CONTROL_MARKER,
                        DEFERRED_MARKER,
                        SOURCE_MARKER,
                        VALUE_MARKER,
                    )

                    # Only the failed, input-only first background control can
                    # be named in a new acceptance. No general Unicode waiver.
                    stages = [r[0] for r in db.execute(
                        "SELECT stage FROM wechat_send_journal WHERE operation_id=? AND stage GLOB 'BG_*' ORDER BY seq",
                        (row["operation_id"],))]
                    units = len(CONTROL_MARKER.encode("utf-16-le")) // 2
                    expected = ["BG_PSEUDO_FOCUS_INTENT"] + [f"BG_CHAR_{n:03d}" for n in range(1, units + 1)]
                    expected += ["BG_PSEUDO_FOCUS_END_INTENT"]
                    fixed_marker = (source["text"] == CONTROL_MARKER and not row["submit_permitted"]
                                    and stages == expected)
                    if source["text"] == VALUE_MARKER:
                        fixed_marker = (not row["submit_permitted"]
                            and row["reason"] == "background_target_became_foreground"
                            and stages == ["BG_PSEUDO_FOCUS_INTENT", "BG_VALUE_INPUT_INTENT"])
                    if source["text"] in {DEFERRED_MARKER, SOURCE_MARKER}:
                        fixed_marker = (not row["submit_permitted"]
                            and row["reason"] == "background_target_became_foreground"
                            and stages == ["BG_VALUE_INPUT_INTENT"])
                    if source["text"] == BMP_MARKER:
                        bmp_units = len(BMP_MARKER.encode("utf-16-le")) // 2
                        bmp_stages = ["BG_PSEUDO_FOCUS_INTENT"] + [f"BG_CHAR_{n:03d}" for n in range(1, bmp_units + 1)]
                        fixed_marker = (row["submit_permitted"] == 1
                            and row["reason"] == "background_target_became_foreground"
                            and stages == bmp_stages + ["BG_MOVE_INTENT", "BG_DOWN_INTENT", "BG_UP_INTENT"])
                if (not source or source["state"] != "UNCONFIRMED" or source["origin"] != "operator"
                        or not fixed_marker):
                    raise SocialError("channel_review_uncertainty_source_invalid")
                seen.add(row["action_id"])
                continue  # Acceptance of uncertainty is NOT delivery evidence or a state change.
            if row["delivery_state"] != "CONFIRMED" or not any(
                    r[0] == row["action_id"] and r[1] == "manual_confirmation" for r in manual):
                raise SocialError("channel_review_prior_delivery_unreconciled")
        elif row["delivery_state"] != "FAILED":
            raise SocialError("channel_review_prior_preinput_unreconciled")
    if seen != set(reviewed_unknowns):
        raise SocialError("channel_review_uncertainty_action_not_unknown")
    payload = [list(map(dict, rows)), [tuple(r) for r in manual]]
    if reviewed_unknowns:
        payload.append(list(reviewed_unknowns))
    return digest(payload)


def authorize(ledger, binding, request_id, marker, facts, *, approval_ref,
              reviewed_unknowns=(), uncertainty_approval_ref=None, backend_kind="foreground", background_readmission=None):
    """Trusted local operator after reviewing a concrete bounded trial.

    The caller obtains facts from fresh native observations, not UI/model JSON.
    This appends a one-use review; the channel is still blocked until consumed.
    """
    import hashlib

    identifier(approval_ref)
    identifier(request_id)
    reviewed_unknowns = _reviewed_actions(reviewed_unknowns, uncertainty_approval_ref)
    admitted = backend_kind in {"integrated_background_bmp", "restarted_background_bmp"}
    if background_readmission is not None:
        from .background_readmission import BackgroundReadmission
        if not admitted or type(background_readmission) is not BackgroundReadmission:
            raise SocialError("background_readmission_context_invalid")
    required = {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                "foreground_hit_and_focus", "no_capture_or_hardware_input", "button_not_pressed",
                "class_entry_matches_pinned_code", "prior_process_generation_exited"}
    if backend_kind in {"standalone_background_control", "standalone_background_value", "standalone_background_deferred",
                        "standalone_background_source", "standalone_background_bmp", "standalone_background_return",
                        "standalone_background_covered"}:
        from .background_contract import (
            BMP_MARKER,
            BMP_REQUEST,
            CONTROL_MARKER,
            CONTROL_REQUEST,
            COVERED_MARKER,
            COVERED_REQUEST,
            DEFERRED_MARKER,
            DEFERRED_REQUEST,
            RETURN_MARKER,
            RETURN_REQUEST,
            SOURCE_MARKER,
            SOURCE_REQUEST,
            VALUE_MARKER,
            VALUE_REQUEST,
        )

        expected = {"standalone_background_control": (CONTROL_MARKER, CONTROL_REQUEST),
                    "standalone_background_value": (VALUE_MARKER, VALUE_REQUEST),
                    "standalone_background_deferred": (DEFERRED_MARKER, DEFERRED_REQUEST),
                    "standalone_background_source": (SOURCE_MARKER, SOURCE_REQUEST),
                    "standalone_background_bmp": (BMP_MARKER, BMP_REQUEST),
                    "standalone_background_return": (RETURN_MARKER, RETURN_REQUEST),
                    "standalone_background_covered": (COVERED_MARKER, COVERED_REQUEST)}[backend_kind]
        if (marker, request_id) != expected:
            raise SocialError("background_review_scope_denied")
        required -= {"foreground_hit_and_focus", "no_capture_or_hardware_input"}
        required |= {"standalone_identity_verified", "background_observer_active", "no_capture"}
        if backend_kind == "standalone_background_covered":
            required.add("covered_by_other_app")
    elif admitted:
        from .background_completion import require_background_admission
        from .background_contract import (
            INTEGRATED_MARKER,
            INTEGRATED_REQUEST,
            RESTART_MARKER,
            RESTART_REQUEST,
        )
        expected = (INTEGRATED_MARKER, INTEGRATED_REQUEST) if backend_kind == "integrated_background_bmp" else (RESTART_MARKER, RESTART_REQUEST)
        if (marker, request_id) != expected:
            raise SocialError("background_review_scope_denied")
        if background_readmission is None:
            require_background_admission(ledger, binding)
        else:
            background_readmission.check(ledger, binding)
        required -= {"foreground_hit_and_focus", "no_capture_or_hardware_input", "prior_process_generation_exited"}
        required |= {"standalone_identity_verified", "background_observer_active", "no_capture", "covered_by_other_app",
                     "prior_input_context_reconciled", "background_admission_verified"}
    elif backend_kind != "foreground":
        raise SocialError("channel_review_backend_denied")
    if (type(facts) is not dict or set(facts) != required | {"observed_at", "binding_generation"}
            or any(facts[k] is not True for k in required) or facts["binding_generation"] != binding.generation):
        raise SocialError("channel_review_live_facts_incomplete")
    timestamp(facts["observed_at"])
    now = ledger.now()
    if not now - 20 <= facts["observed_at"] <= now:
        raise SocialError("channel_review_live_facts_expired")
    from .store import BridgeStore

    with ledger._transaction() as db:
        BridgeStore._binding(db, binding)
        reason = db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        if reason != "submission_or_draft_requires_review":
            raise SocialError("channel_review_block_reason_not_supported")
        prior = db.execute("SELECT window_binding FROM wechat_send_operations WHERE scope=? AND input_permitted=1",
                           (binding.scope.key(),)).fetchall()
        if not admitted and (not prior or any(json.loads(r[0])["created_filetime"] == binding.account.window.created_filetime
                            and json.loads(r[0])["pid"] == binding.account.window.pid for r in prior)):
            raise SocialError("channel_review_requires_new_process_generation")
        if db.execute("SELECT 1 FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchone():
            raise SocialError("channel_review_trial_already_active")
        prior_hash = history(db, binding.scope.key(), reviewed_unknowns=reviewed_unknowns)
        if db.execute("SELECT 1 FROM wechat_send_operations WHERE scope=? AND content_sha256=?",
                      (binding.scope.key(), hashlib.sha256(marker.encode()).hexdigest())).fetchone():
            raise SocialError("channel_review_old_marker_replay_denied")
        stored_facts = dict(facts)
        if background_readmission is not None:
            stored_facts["background_revalidation"] = background_readmission.descriptor
        if backend_kind != "foreground":
            stored_facts["backend_kind"] = backend_kind
        if reviewed_unknowns:
            stored_facts["prior_uncertainty_review"] = dict(approval_ref=uncertainty_approval_ref, action_ids=list(reviewed_unknowns))
        review_id = digest([binding.generation, request_id, marker, approval_ref, stored_facts])
        db.execute("INSERT INTO wechat_channel_reviews VALUES(?,?,?,?,?,?,?,?,?,?,NULL,0)",
            (review_id, binding.scope.key(), binding.generation, request_id, hashlib.sha256(marker.encode()).hexdigest(),
             prior_hash, json.dumps(stored_facts, sort_keys=True), approval_ref, now, now + 60))
    return review_id


def consume(db, ledger, binding, request_id, marker, review_id, action_id, *, background_readmission=None):
    import hashlib

    row = db.execute("SELECT * FROM wechat_channel_reviews WHERE review_id=?", (review_id,)).fetchone()
    reviewed = ()
    if row:
        accepted = json.loads(row["facts"]).get("prior_uncertainty_review")
        if accepted is not None:
            if type(accepted) is not dict or set(accepted) != {"approval_ref", "action_ids"} or type(accepted["action_ids"]) is not list:
                raise SocialError("channel_review_uncertainty_scope_invalid")
            reviewed = _reviewed_actions(tuple(accepted["action_ids"]), accepted["approval_ref"])
    now = ledger.now()
    if (not row or row["action_id"] is not None or row["finished"] or row["scope"] != binding.scope.key()
            or row["generation"] != binding.generation or row["request_id"] != request_id
            or row["marker_sha256"] != hashlib.sha256(marker.encode()).hexdigest()
            or not row["approved_at"] <= now <= row["expires_at"]
            or row["history_sha256"] != history(db, binding.scope.key(), reviewed_unknowns=reviewed)):
        raise SocialError("channel_review_invalid_expired_or_consumed")
    if db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] != "submission_or_draft_requires_review":
        raise SocialError("channel_review_block_changed")
    stored = json.loads(row["facts"])
    if stored.get("backend_kind") in {"integrated_background_bmp", "restarted_background_bmp"}:
        from .background_completion import require_background_admission
        if "background_revalidation" in stored:
            from .background_readmission import BackgroundReadmission
            if (type(background_readmission) is not BackgroundReadmission
                    or stored["background_revalidation"] != background_readmission.descriptor):
                raise SocialError("background_readmission_context_invalid")
            background_readmission.check(ledger, binding)
        else:
            require_background_admission(ledger, binding)
    db.execute("UPDATE wechat_channel_reviews SET action_id=? WHERE review_id=?", (action_id, review_id))
    db.execute("UPDATE wechat_send_control SET blocked_reason=NULL")


def close_trial(ledger, review_id):
    with ledger._transaction() as db:
        row = db.execute("SELECT action_id,finished FROM wechat_channel_reviews WHERE review_id=?", (review_id,)).fetchone()
        if row and row["action_id"] is not None and not row["finished"]:
            db.execute("UPDATE wechat_channel_reviews SET finished=1 WHERE review_id=?", (review_id,))
            db.execute("UPDATE wechat_send_control SET blocked_reason='reviewed_trial_requires_review'")


def recover(db):
    rows = db.execute("SELECT review_id,action_id FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchall()
    if rows:
        db.execute("UPDATE wechat_send_control SET blocked_reason='interrupted_reviewed_trial'")
        for review_id, action_id in rows:
            db.execute("UPDATE wechat_channel_reviews SET finished=1 WHERE review_id=?", (review_id,))
            db.execute("UPDATE outbox SET state='CANCELLED',reason='interrupted_reviewed_trial' WHERE action_id=? AND state='QUEUED'", (action_id,))
