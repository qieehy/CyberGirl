"""B18 single-use intents in the ORIGINAL SendState ledger, with no UI effects.

No new tables or general channel unblock. An action must already have passed
the original queue/claim/begin and review gates. Cleanup alone remains allowed
when a pause or newer message has invalidated further input/submission.
"""

import hashlib

from ...social.models import SocialError
from .background_contract import (
    BMP_MARKER,
    CONTROL_MARKER,
    COVERED_MARKER,
    DEFERRED_MARKER,
    INTEGRATED_MARKER,
    RESTART_MARKER,
    RETURN_MARKER,
    SOURCE_MARKER,
    VALUE_MARKER,
)
from .background_text import require_background_bmp_text
from .contracts import require_send_target


class BackgroundIntentJournal:
    def workbench_action(self, state, action):
        return False

    def __init__(self, state, operation_id, binding, action):
        workbench = self.workbench_action(state, action)
        if not workbench and action.text not in {BMP_MARKER, RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER, CONTROL_MARKER, VALUE_MARKER, DEFERRED_MARKER, SOURCE_MARKER}:
            raise SocialError("background_journal_marker_denied")
        if workbench or action.text in {BMP_MARKER, RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER}:
            require_background_bmp_text(action.text)
        self.state, self.operation_id, self.binding, self.action = state, operation_id, binding, action
        self.units = len(action.text.encode("utf-16-le")) // 2
        self.value_mode = not workbench and action.text in {VALUE_MARKER, DEFERRED_MARKER, SOURCE_MARKER}
        self.deferred = not workbench and action.text in {DEFERRED_MARKER, SOURCE_MARKER}
        self.diagnostic_only = not workbench and action.text == SOURCE_MARKER
        self.return_mode = workbench or action.text in {RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER}
        self.failed = False
        with state.ledger._transaction() as db:
            self._operation(db)
            if self._stages(db):
                raise SocialError("background_journal_already_used")

    def _operation(self, db):
        binding, action = self.binding, self.action
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=binding.account.window)
        row = db.execute("SELECT * FROM wechat_send_operations WHERE operation_id=?", (self.operation_id,)).fetchone()
        if (not row or row["action_id"] != action.action_id or row["scope"] != binding.scope.key()
                or row["generation"] != binding.generation or row["finished"]
                or row["content_sha256"] != hashlib.sha256(action.text.encode()).hexdigest()):
            raise SocialError("background_journal_operation_invalid")
        return row

    def _stages(self, db):
        return [row[0] for row in db.execute(
            "SELECT stage FROM wechat_send_journal WHERE operation_id=? AND stage GLOB 'BG_*' ORDER BY seq",
            (self.operation_id,))]

    def record(self, stage):
        try:
            return self._record(stage)
        except BaseException:
            self.failed = True
            raise

    def _record(self, stage):
        if self.diagnostic_only and stage != "VALUE_INPUT_INTENT":
            raise SocialError("background_source_effect_denied")
        if ((self.return_mode and stage in {"MOVE_INTENT", "DOWN_INTENT", "UP_INTENT"})
                or (not self.return_mode and stage in {"RETURN_DOWN_INTENT", "RETURN_UP_INTENT"})):
            raise SocialError("background_journal_submit_method_denied")
        if stage not in {"PSEUDO_FOCUS_INTENT", "CHAR_INTENT", "MOVE_INTENT", "DOWN_INTENT",
                         "UP_INTENT", "PSEUDO_FOCUS_END_INTENT", "VALUE_INPUT_INTENT", "RETURN_DOWN_INTENT", "RETURN_UP_INTENT"}:
            raise SocialError("background_journal_stage_denied")
        cleanup = stage == "PSEUDO_FOCUS_END_INTENT"
        if self.failed and not cleanup:
            raise SocialError("background_journal_prior_failure")
        with self.state.ledger._transaction() as db:
            row = self._operation(db)
            stages = self._stages(db)
            chars = [s for s in stages if s.startswith("BG_CHAR_")]
            full_input = ["BG_PSEUDO_FOCUS_INTENT"] + [f"BG_CHAR_{i:03d}" for i in range(1, self.units + 1)]
            if self.value_mode:
                full_input = ["BG_PSEUDO_FOCUS_INTENT", "BG_VALUE_INPUT_INTENT"]
            if self.deferred:
                full_input.reverse()
            if cleanup:
                if "BG_PSEUDO_FOCUS_INTENT" not in stages or "BG_PSEUDO_FOCUS_END_INTENT" in stages:
                    raise SocialError("background_journal_cleanup_order")
            else:
                # Pause/TTL/new message/generation checked transactionally for
                # every intent; do not infer permission from the first check.
                self.state._action(db, self.binding, self.action)
                if stage == "PSEUDO_FOCUS_INTENT":
                    if self.deferred:
                        if stages != ["BG_VALUE_INPUT_INTENT"] or row["phase"] != "INPUT_PERMITTED":
                            raise SocialError("background_journal_deferred_focus_order")
                    elif stages or row["phase"] != "PRECHECK":
                        raise SocialError("background_journal_input_already_used")
                    if not self.deferred:
                        db.execute("UPDATE wechat_send_operations SET phase='INPUT_PERMITTED',input_permitted=1 WHERE operation_id=?",
                                   (self.operation_id,))
                elif stage == "VALUE_INPUT_INTENT":
                    expected_stages = [] if self.deferred else ["BG_PSEUDO_FOCUS_INTENT"]
                    expected_phase = "PRECHECK" if self.deferred else "INPUT_PERMITTED"
                    if not self.value_mode or stages != expected_stages or row["phase"] != expected_phase:
                        raise SocialError("background_journal_input_method_denied")
                    if self.deferred:
                        db.execute("UPDATE wechat_send_operations SET phase='INPUT_PERMITTED',input_permitted=1 WHERE operation_id=?",
                                   (self.operation_id,))
                elif stage == "CHAR_INTENT":
                    if (self.value_mode or len(chars) >= self.units or stages != full_input[:len(chars) + 1]
                            or row["phase"] != "INPUT_PERMITTED"):
                        raise SocialError("background_journal_char_order")
                else:
                    previous = {"MOVE_INTENT": [], "DOWN_INTENT": ["BG_MOVE_INTENT"],
                                "UP_INTENT": ["BG_MOVE_INTENT", "BG_DOWN_INTENT"],
                                "RETURN_DOWN_INTENT": [], "RETURN_UP_INTENT": ["BG_RETURN_DOWN_INTENT"]}[stage]
                    if stages != full_input + previous:
                        raise SocialError("background_journal_submit_order")
                    expected_phase = "INPUT_PERMITTED" if stage in {"MOVE_INTENT", "RETURN_DOWN_INTENT"} else "SUBMIT_PERMITTED"
                    if row["phase"] != expected_phase:
                        raise SocialError("background_journal_submit_phase")
                    if stage in {"MOVE_INTENT", "RETURN_DOWN_INTENT"}:
                        db.execute("UPDATE wechat_send_operations SET phase='SUBMIT_PERMITTED',submit_permitted=1 WHERE operation_id=?",
                                   (self.operation_id,))
            persisted = f"BG_CHAR_{len(chars) + 1:03d}" if stage == "CHAR_INTENT" else "BG_" + stage
            db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,?,?)",
                       (self.operation_id, persisted, self.state.ledger.now()))
        return True
