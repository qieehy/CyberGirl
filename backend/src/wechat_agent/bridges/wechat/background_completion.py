"""Original-ledger control completion, separate from delivery confirmation.

No UI, process access, new tables, sending permit or general channel unblock.
The trusted parent supplies its own child result and cleanup observations.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict

from ...social.models import SocialError
from .background_contract import COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER
from .background_text import require_background_bmp_text

CLEAN_STAGE = "BG_BMP_CONTROL_CLEAN_V1"
_CONTROL_FIELDS = {"state", "reason", "input_possible", "submit_possible", "focus_cleanup_verified",
                   "input_units_attempted", "posts_attempted", "upstream_ok", "delivery_verified"}
_OBS_FIELDS = {"foreground_target_seen", "foreground_events", "pointer_samples", "pointer_position_changes",
               "requested_sample_interval_ms", "maximum_sample_gap_ms", "pointer_change_attribution", "failure",
               "target_foreground_phases", "target_detection_sources", "target_foreground_sampled",
               "target_foreground_events", "target_foreground_events_dropped", "hook_released", "observer_stopped"}


def project_background_result(result):
    """Bounded, typed diagnostics from an owned child; no arbitrary UI fields."""
    if type(result) is not dict or set(result) != {"control_result", "worker_local_cleanup_verified", "observation"}:
        raise SocialError("background_result_invalid")
    control, observation = result["control_result"], result["observation"]
    if (type(control) is not dict or set(control) != _CONTROL_FIELDS
            or type(observation) is not dict or set(observation) != _OBS_FIELDS
            or type(result["worker_local_cleanup_verified"]) is not bool
            or control["state"] not in {"NOT_STARTED", "UNKNOWN", "REQUESTED"}
            or type(control["reason"]) is not str or not re.fullmatch(r"[a-z0-9_]{1,128}", control["reason"])):
        raise SocialError("background_result_invalid")
    for key in ("input_possible", "submit_possible", "focus_cleanup_verified", "delivery_verified"):
        if type(control[key]) is not bool:
            raise SocialError("background_result_invalid")
    for key in ("input_units_attempted", "posts_attempted"):
        if type(control[key]) is not int or not 0 <= control[key] <= 102:
            raise SocialError("background_result_invalid")
    if control["upstream_ok"] is not None and type(control["upstream_ok"]) is not bool:
        raise SocialError("background_result_invalid")
    if observation["failure"] is not None and (type(observation["failure"]) is not str
            or not re.fullmatch(r"[a-z0-9_]{1,128}", observation["failure"])):
        raise SocialError("background_result_invalid")
    try:
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded) > 16384:
            raise ValueError()
        return json.loads(encoded)
    except (TypeError, ValueError):
        raise SocialError("background_result_invalid") from None


def clean_background_result(result, text, *, allow_foreground=False):
    """A UI control fact only; a true result never asserts delivered text."""
    require_background_bmp_text(text)
    if type(result) is not dict or set(result) != {"control_result", "worker_local_cleanup_verified", "observation"}:
        return False
    control, observation = result["control_result"], result["observation"]
    if (type(control) is not dict or set(control) != _CONTROL_FIELDS
            or type(observation) is not dict or set(observation) != _OBS_FIELDS
            or result["worker_local_cleanup_verified"] is not True):
        return False
    units = len(text.encode("utf-16-le")) // 2
    if (control["state"] != "REQUESTED" or control["reason"] != "sent"
            or any(control[k] is not True for k in ("input_possible", "submit_possible", "focus_cleanup_verified", "upstream_ok"))
            or control["delivery_verified"] is not False
            or type(control["input_units_attempted"]) is not int or control["input_units_attempted"] != units
            or type(control["posts_attempted"]) is not int or control["posts_attempted"] != units + 4):
        return False
    for key in ("foreground_events", "pointer_samples", "pointer_position_changes", "target_foreground_events_dropped"):
        if type(observation[key]) is not int or not 0 <= observation[key] <= 1_000_000:
            return False
    for key in ("requested_sample_interval_ms", "maximum_sample_gap_ms"):
        if type(observation[key]) not in (int, float) or not math.isfinite(observation[key]):
            return False
    foreground_ok = (observation["foreground_target_seen"] is False and observation["target_foreground_sampled"] is False
                     and observation["target_foreground_phases"] == [] and observation["target_detection_sources"] == []
                     and observation["target_foreground_events"] == [] and observation["target_foreground_events_dropped"] == 0)
    if allow_foreground is True:
        foreground_ok = (
            type(observation["foreground_target_seen"]) is bool
            and type(observation["target_foreground_sampled"]) is bool
            and type(observation["target_foreground_phases"]) is list
            and len(observation["target_foreground_phases"]) <= 16
            and all(isinstance(p, str) and len(p) <= 64 for p in observation["target_foreground_phases"])
            and type(observation["target_detection_sources"]) is list
            and all(isinstance(s, str) and s in {"event", "sample", "guard_current", "event_current"}
                    for s in observation["target_detection_sources"])
            and type(observation["target_foreground_events"]) is list
            and len(observation["target_foreground_events"]) <= 16
            and all(type(e) is dict and set(e) == {"phase", "object_id", "child_id", "window_self_event", "current_foreground_is_target", "event_window_is_current"}
                    and isinstance(e["phase"], str) and len(e["phase"]) <= 64
                    and all(type(e[k]) is int for k in ("object_id", "child_id"))
                    and all(type(e[k]) is bool for k in ("window_self_event", "current_foreground_is_target", "event_window_is_current"))
                    for e in observation["target_foreground_events"]))
    return (foreground_ok
            and observation["failure"] is None and observation["hook_released"] is True
            and observation["observer_stopped"] is True and observation["pointer_samples"] > 0
            and 5 <= observation["requested_sample_interval_ms"] <= 50
            and 0 <= observation["maximum_sample_gap_ms"] <= 250
            and observation["pointer_change_attribution"] == "not_determined")


def record_background_completion(ledger, operation_id, result, *, reaped, restored, locks_released):
    if not all(value is True for value in (reaped, restored, locks_released)):
        raise SocialError("background_completion_cleanup_incomplete")
    with ledger._transaction() as db:
        row = db.execute("""SELECT s.*,o.text FROM wechat_send_operations s JOIN outbox o USING(action_id)
                            WHERE s.operation_id=?""", (operation_id,)).fetchone()
        from ...workbench.authorization import authorized

        if (not row or (row["text"] not in {COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER}
                        and not authorized(db, row["action_id"], row["text"]))
                or not all(row[k] == 1 for k in ("finished", "worker_reaped", "restore_verified", "input_permitted", "submit_permitted"))
                or row["content_sha256"] != hashlib.sha256(row["text"].encode()).hexdigest()
                or not clean_background_result(result, row["text"])):
            raise SocialError("background_completion_evidence_incomplete")
        stages = [r[0] for r in db.execute("""SELECT stage FROM wechat_send_journal
            WHERE operation_id=? AND stage GLOB 'BG_*' ORDER BY seq""", (operation_id,))]
        expected = ["BG_PSEUDO_FOCUS_INTENT"] + [f"BG_CHAR_{n:03d}" for n in range(1, len(row["text"]) + 1)]
        expected += ["BG_RETURN_DOWN_INTENT", "BG_RETURN_UP_INTENT", "BG_PSEUDO_FOCUS_END_INTENT"]
        if stages == expected + [CLEAN_STAGE]:
            return False
        if stages != expected:
            raise SocialError("background_completion_intents_incomplete")
        db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,?,?)",
                   (operation_id, CLEAN_STAGE, ledger.now()))
    return True


def _require_generation_clean(ledger, binding, *, require_covered=True):
    """Check every input in one native generation; never waive uncertainty."""
    expected_window = asdict(binding.account.window)
    rows = ledger._db.execute("""SELECT s.*,o.text,o.state AS delivery_state FROM wechat_send_operations s
        JOIN outbox o USING(action_id) WHERE s.scope=? ORDER BY s.created_at""", (binding.scope.key(),)).fetchall()
    if not rows or len(rows) > 100:
        raise SocialError("background_admission_missing_or_unbounded")
    admitted = False
    for row in rows:
        old = json.loads(row["window_binding"])
        if (old["pid"], old["created_filetime"]) != (expected_window["pid"], expected_window["created_filetime"]):
            continue
        if not row["input_permitted"]:
            continue
        complete = ledger._db.execute("SELECT 1 FROM wechat_send_journal WHERE operation_id=? AND stage=?",
                                      (row["operation_id"], CLEAN_STAGE)).fetchone()
        manual = ledger._db.execute("SELECT 1 FROM delivery_evidence WHERE action_id=? AND kind='manual_confirmation'",
                                    (row["action_id"],)).fetchone()
        echoes = [json.loads(item[0]) for item in ledger._db.execute(
            "SELECT payload FROM delivery_evidence WHERE action_id=? AND kind='local_echo'", (row["action_id"],))]
        echo_ok = (len(echoes) == 1 and (echoes[0].get("reference"), echoes[0].get("content_sha256"),
                                       echoes[0].get("binding_generation")) == (
                       row["operation_id"], row["content_sha256"], row["generation"])
                   and bool(echoes[0].get("platform_message_id")))
        if (old != expected_window or row["delivery_state"] != "CONFIRMED" or not complete or not manual
                or not echo_ok or row["generation"] != binding.generation
                or not all(row[k] == 1 for k in ("finished", "worker_reaped", "restore_verified"))):
            raise SocialError("background_current_generation_unreconciled")
        if row["text"] == COVERED_MARKER:
            admitted = True
    if require_covered and not admitted:
        raise SocialError("background_admission_missing_or_unbounded")
    return True


def require_background_admission(ledger, binding):
    """Default remains restricted to the originally admitted native generation."""
    return _require_generation_clean(ledger, binding)
