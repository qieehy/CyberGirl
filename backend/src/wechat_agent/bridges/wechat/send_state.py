"""Durable single-operation permits in the parent Ledger; no native UI access."""

import hashlib
import json
import re
import uuid
from dataclasses import asdict

from ...social.models import OutgoingAction, SocialError, identifier
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
from .contracts import Cursor, DeliveryEvidence, SourceRecord, digest, require_send_target
from .store import BridgeStore


def migrate(db):
    db.execute("ALTER TABLE inbox ADD COLUMN origin TEXT NOT NULL DEFAULT 'source'")
    db.execute("""CREATE TABLE wechat_send_operations(
        operation_id TEXT PRIMARY KEY,action_id TEXT UNIQUE NOT NULL REFERENCES outbox(action_id),
        scope TEXT NOT NULL,generation TEXT NOT NULL,content_sha256 TEXT NOT NULL,
        baselines TEXT NOT NULL,window_binding TEXT NOT NULL,phase TEXT NOT NULL,input_permitted INTEGER NOT NULL DEFAULT 0,
        submit_permitted INTEGER NOT NULL DEFAULT 0,finished INTEGER NOT NULL DEFAULT 0,
        worker_reaped INTEGER,restore_verified INTEGER,gate_original INTEGER,reason TEXT,created_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE wechat_send_journal(
        seq INTEGER PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES wechat_send_operations(operation_id),
        stage TEXT NOT NULL,recorded_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE wechat_send_control(
        id INTEGER PRIMARY KEY CHECK(id=1),blocked_reason TEXT)""")
    db.execute("INSERT INTO wechat_send_control VALUES(1,NULL)")
    db.execute("PRAGMA user_version=6")


def recover(db):
    # Even PRECHECK could have left a temporarily enabled provider after a crash.
    # Never assume a restarted process has reaped the previous native worker.
    if db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0").fetchone():
        db.execute("UPDATE wechat_send_control SET blocked_reason='interrupted_ui_operation'")
        db.execute("""UPDATE wechat_send_operations SET finished=1,phase='INTERRUPTED',
                      reason='interrupted_ui_operation' WHERE finished=0""")


class SendState:
    def __init__(self, ledger):
        self.ledger = ledger

    def queue_marker(self, binding, request_id, marker, *, review_id=None, background_readmission=None):
        """Trusted local operator only, no fake platform sender or model decision.

        The first filehelper PoC cannot reply to a fabricated incoming message:
        real phone messages in this conversation are correctly self echoes.
        """
        identifier(request_id)
        if (len(request_id) > 64 or not isinstance(marker, str)
                or marker not in {BMP_MARKER, RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER, CONTROL_MARKER, VALUE_MARKER, DEFERRED_MARKER, SOURCE_MARKER} and not re.fullmatch(r"W2B-[A-Za-z0-9-]{1,64}", marker)):
            raise SocialError("invalid_fixed_test_marker")
        action = OutgoingAction(digest([binding.scope.key(), request_id, "operator-send"]),
                                binding.scope, "operator:" + request_id, marker)
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=binding.account.window)
        event_id = digest([binding.scope.key(), "operator", request_id])
        fingerprint = digest([binding.generation, marker, request_id])
        with self.ledger._transaction() as db:
            bound = BridgeStore._binding(db, binding)
            old = db.execute("SELECT fingerprint,origin FROM inbox WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint or old["origin"] != "operator":
                    raise SocialError("operator_request_conflict")
                return action.action_id  # Same request never creates another action/attempt.
            grant = db.execute("SELECT * FROM scopes WHERE scope=?", (binding.scope.key(),)).fetchone()
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            if bound["mode"] != "draft" or not grant["enabled"] or control["paused"]:
                raise SocialError("operator_send_not_enabled")
            if review_id is not None:
                from .channel_review import consume

                consume(db, self.ledger, binding, request_id, marker, review_id, action.action_id, background_readmission=background_readmission)
            elif (db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
                    or db.execute("SELECT 1 FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchone()):
                raise SocialError("ui_channel_requires_review")
            if db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] >= 10000:
                raise SocialError("inbox_capacity")
            db.execute("UPDATE scopes SET context_version=context_version+1 WHERE scope=?", (binding.scope.key(),))
            self.ledger._cancel_queued(db, binding.scope.key(), "new_operator_request")
            now = self.ledger.now()
            db.execute("""INSERT INTO inbox(event_id,scope,message_id,fingerprint,text,sender_id,occurred_at,
                source_verified,is_self,mention_self,received_at,policy_revision,global_revision,context_version,
                state,reason,persona_version,bridge_generation,origin)
                VALUES(?,?,?,?,?,'local-operator',?,0,0,0,?,?,?,?, 'DECIDED','local_fixed_marker',?,?,'operator')""",
                (event_id, binding.scope.key(), action.reply_to, fingerprint, "本机固定标记发送请求", now, now,
                 grant["revision"], control["revision"], grant["context_version"] + 1,
                 control["persona_version"], binding.generation))
            db.execute("INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,?,?,'QUEUED',?)",
                       (action.action_id, event_id, marker, now + 120))
        return action.action_id

    def _action(self, db, binding, action):
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=binding.account.window)
        BridgeStore._binding(db, binding)
        row = db.execute("""SELECT i.*,o.action_id,o.text AS reply,o.expires_at,o.state AS delivery_state
            FROM inbox i JOIN outbox o USING(event_id) WHERE o.action_id=?""", (action.action_id,)).fetchone()
        if (not row or row["delivery_state"] != "SENDING" or row["scope"] != binding.scope.key()
                or row["bridge_generation"] != binding.generation or row["reply"] != action.text
                or row["message_id"] != action.reply_to or row["expires_at"] <= self.ledger.now()
                or not self.ledger._valid(db, row)):
            raise SocialError("stale_send_action")
        return row

    def begin(self, binding, action, baselines):
        if (type(baselines) is not dict or not 1 <= len(baselines) <= 8
                or any(not isinstance(k, str) or not k or len(k) > 128 or not isinstance(v, Cursor)
                       for k, v in baselines.items())):
            raise SocialError("invalid_echo_baseline")
        payload = json.dumps({k: [v.sort_seq, v.local_id] for k, v in baselines.items()}, sort_keys=True)
        operation_id = uuid.uuid4().hex
        with self.ledger._transaction() as db:
            self._action(db, binding, action)
            trial = db.execute("SELECT action_id FROM wechat_channel_reviews WHERE action_id IS NOT NULL AND finished=0").fetchone()
            if trial and trial[0] != action.action_id:
                raise SocialError("different_reviewed_trial_active")
            if db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]:
                raise SocialError("ui_channel_requires_review")
            if db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0").fetchone():
                raise SocialError("ui_operation_busy")
            if db.execute("SELECT 1 FROM wechat_send_operations WHERE action_id=?", (action.action_id,)).fetchone():
                raise SocialError("send_operation_already_used")
            if db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] >= 10000:
                raise SocialError("send_operation_capacity")
            db.execute("""INSERT INTO wechat_send_operations(
                operation_id,action_id,scope,generation,content_sha256,baselines,window_binding,phase,created_at)
                VALUES(?,?,?,?,?,?,?,'PRECHECK',?)""", (operation_id, action.action_id, binding.scope.key(),
                binding.generation, hashlib.sha256(action.text.encode()).hexdigest(), payload,
                json.dumps(asdict(binding.account.window), sort_keys=True), self.ledger.now()))
        return operation_id

    def journal(self, operation_id, stage, *, original):
        if (stage not in {"GATE_CHECKED", "GATE_ENABLE_INTENT", "GATE_ENABLED", "GATE_RESTORE_INTENT", "GATE_RESTORED", "NATIVE_PREPARED"}
                or type(original) is not int or original not in (0, 1)):
            raise SocialError("invalid_send_journal_stage")
        with self.ledger._transaction() as db:
            row = db.execute("SELECT finished FROM wechat_send_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row or row[0]:
                raise SocialError("inactive_send_operation")
            db.execute("UPDATE wechat_send_operations SET gate_original=? WHERE operation_id=?",
                       (original, operation_id))
            db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,?,?)",
                       (operation_id, stage, self.ledger.now()))

    def permit(self, operation_id, binding, action, phase):
        if action.text == SOURCE_MARKER and phase == "SUBMIT_PERMITTED":
            raise SocialError("background_source_submission_denied")
        prior = {"INPUT_PERMITTED": "PRECHECK", "SUBMIT_PERMITTED": "INPUT_PERMITTED"}.get(phase)
        if prior is None:
            raise SocialError("invalid_send_phase")
        with self.ledger._transaction() as db:
            self._action(db, binding, action)  # Recheck pause, TTL, new input and binding at each side effect.
            column = "input_permitted" if phase == "INPUT_PERMITTED" else "submit_permitted"
            changed = db.execute(f"""UPDATE wechat_send_operations SET phase=?,{column}=1
                WHERE operation_id=? AND action_id=? AND generation=? AND phase=? AND finished=0""",
                (phase, operation_id, action.action_id, binding.generation, prior)).rowcount
            if changed != 1:
                raise SocialError("send_permit_already_used")

    def permit_focus(self, operation_id, binding, action):
        with self.ledger._transaction() as db:
            self._action(db, binding, action)
            row = db.execute("SELECT phase,finished FROM wechat_send_operations WHERE operation_id=? AND action_id=?",
                             (operation_id, action.action_id)).fetchone()
            if (not row or row["finished"] or row["phase"] != "PRECHECK" or db.execute(
                    "SELECT 1 FROM wechat_send_journal WHERE operation_id=? AND stage='FOCUS_PERMITTED'", (operation_id,)).fetchone()):
                raise SocialError("focus_permit_already_used")
            db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,'FOCUS_PERMITTED',?)",
                       (operation_id, self.ledger.now()))

    def _gesture(self, db, operation_id, binding, action):
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=binding.account.window)
        if not re.fullmatch(r"W2B-[A-Za-z0-9-]{1,64}", action.text):
            raise SocialError("gesture_fixed_marker_required")
        row = db.execute("""SELECT s.*,i.origin FROM wechat_send_operations s
            JOIN outbox o USING(action_id) JOIN inbox i USING(event_id) WHERE operation_id=?""", (operation_id,)).fetchone()
        if (not row or row["action_id"] != action.action_id or row["scope"] != binding.scope.key()
                or row["generation"] != binding.generation or row["finished"] or row["origin"] != "operator"
                or row["phase"] != "SUBMIT_PERMITTED" or not row["submit_permitted"]
                or row["content_sha256"] != hashlib.sha256(action.text.encode()).hexdigest()):
            raise SocialError("gesture_operation_mismatch")
        return {r[0] for r in db.execute("SELECT stage FROM wechat_send_journal WHERE operation_id=?",
                                       (operation_id,)) if r[0].startswith("CLICK_")}

    def gesture_intent(self, operation_id, binding, action, stage):
        """Parent-only durable permit. Return only after FULL SQLite commit."""
        required = {"CLICK_BEGIN_INTENT": None, "CLICK_MOVE_INTENT": "CLICK_BEGIN_INTENT", "CLICK_DOWN_INTENT": "CLICK_BEGIN_INTENT",
                    "CLICK_UP_INTENT": "CLICK_DOWN_INTENT", "CLICK_CANCEL_INTENT": "CLICK_DOWN_INTENT"}
        if stage not in required:
            raise SocialError("invalid_gesture_intent")
        with self.ledger._transaction() as db:
            stages = self._gesture(db, operation_id, binding, action)
            if (stage in stages or any(s.startswith("CLICK_RESULT_") for s in stages)
                    or required[stage] is not None and required[stage] not in stages
                    or stage == "CLICK_BEGIN_INTENT" and stages
                    or stage == "CLICK_MOVE_INTENT" and "CLICK_DOWN_INTENT" in stages
                    or stage in {"CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"} and "CLICK_CANCEL_INTENT" in stages):
                raise SocialError("gesture_intent_already_used_or_out_of_order")
            if stage != "CLICK_CANCEL_INTENT":
                self._action(db, binding, action)  # TTL, pause, policy and new-message fences.
            db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,?,?)",
                       (operation_id, stage, self.ledger.now()))

    def gesture_result(self, operation_id, binding, action, result):
        from .directed_click import ClickResult

        if not isinstance(result, ClickResult):
            raise SocialError("invalid_gesture_result")
        with self.ledger._transaction() as db:
            stages = self._gesture(db, operation_id, binding, action)
            if (any(s.startswith("CLICK_RESULT_") for s in stages)
                    or result.move_attempted != ("CLICK_MOVE_INTENT" in stages)
                    or "CLICK_MOVE_INTENT" in stages and result.message_mode != "queued"
                    or result.down_attempted and "CLICK_DOWN_INTENT" not in stages
                    or result.up_attempted and "CLICK_UP_INTENT" not in stages
                    or result.state == "REQUESTED" and "CLICK_CANCEL_INTENT" in stages
                    or result.state == "NOT_STARTED" and ("CLICK_MOVE_INTENT" in stages or "CLICK_DOWN_INTENT" in stages)):
                raise SocialError("gesture_result_conflicts_with_journal")
            db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,?,?)",
                       (operation_id, "CLICK_RESULT_" + result.state, self.ledger.now()))
            if result.message_mode == "queued":
                db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,'CLICK_MODE_QUEUED',?)",
                           (operation_id, self.ledger.now()))
            if result.message_mode == "injected":
                db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,'CLICK_MODE_INJECTED',?)",
                           (operation_id, self.ledger.now()))
            db.execute("UPDATE wechat_send_operations SET reason=? WHERE operation_id=?", (result.reason, operation_id))
            if result.cancel_acknowledged:
                db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,'CLICK_CANCEL_ACKNOWLEDGED',?)",
                           (operation_id, self.ledger.now()))
            if result.cancel_enqueued:
                db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES(?,'CLICK_CANCEL_ENQUEUED',?)",
                           (operation_id, self.ledger.now()))

    def gesture_summary(self, operation_id):
        """Minimal recovery facts; uncertainty never grants another submit permit."""
        operation = self.ledger._db.execute("SELECT submit_permitted FROM wechat_send_operations WHERE operation_id=?",
                                           (operation_id,)).fetchone()
        if operation is None:
            raise SocialError("gesture_operation_not_found")
        rows = self.ledger._db.execute("SELECT stage FROM wechat_send_journal WHERE operation_id=? AND stage LIKE 'CLICK_%' ORDER BY seq",
                                       (operation_id,)).fetchall()
        stages = [r[0] for r in rows]
        return {"stages": stages, "state": next((s.removeprefix("CLICK_RESULT_") for s in reversed(stages)
            if s.startswith("CLICK_RESULT_")), "UNKNOWN" if operation["submit_permitted"] else "NOT_STARTED"),
            "may_have_reached_target": bool({"CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT"}.intersection(stages)),
            "message_mode": "injected" if "CLICK_MODE_INJECTED" in stages else "queued" if {"CLICK_MOVE_INTENT", "CLICK_MODE_QUEUED"}.intersection(stages) else
                            "synchronous" if any(s.startswith("CLICK_RESULT_") for s in stages)
                            else "unobserved",
            "cancel_acknowledged": "CLICK_CANCEL_ACKNOWLEDGED" in stages,
            "cancel_enqueued": "CLICK_CANCEL_ENQUEUED" in stages}

    def finish(self, operation_id, *, reaped, restored, reason):
        with self.ledger._transaction() as db:
            row = db.execute("SELECT * FROM wechat_send_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row or row["finished"]:
                raise SocialError("inactive_send_operation")
            blocked = ("ui_worker_not_reaped" if not reaped else "accessibility_restore_failed" if not restored
                       else "submission_or_draft_requires_review" if row["input_permitted"] else None)
            db.execute("""UPDATE wechat_send_operations SET phase='FINISHED',finished=1,
                worker_reaped=?,restore_verified=?,reason=COALESCE(reason,?) WHERE operation_id=?""",
                (int(reaped), int(restored), reason, operation_id))
            if blocked:
                db.execute("UPDATE wechat_send_control SET blocked_reason=?", (blocked,))
            trial = db.execute("UPDATE wechat_channel_reviews SET finished=1 WHERE action_id=? AND finished=0", (row["action_id"],)).rowcount
            if trial and not blocked:
                db.execute("UPDATE wechat_send_control SET blocked_reason='reviewed_trial_requires_review'")
            return bool(row["input_permitted"])

    def local_echo(self, binding, action_id, records):
        """Append a uniquely matched post-baseline self echo, never confirm delivery."""
        if type(records) is not tuple or len(records) > 800:
            raise SocialError("invalid_echo_batch")
        with self.ledger._transaction() as db:
            row = db.execute("""SELECT s.*,o.text,i.message_id FROM wechat_send_operations s
                JOIN outbox o USING(action_id) JOIN inbox i USING(event_id) WHERE s.action_id=?""", (action_id,)).fetchone()
            if not row or not row["finished"] or not row["submit_permitted"] or row["generation"] != binding.generation:
                raise SocialError("echo_operation_mismatch")
            action = OutgoingAction(action_id, binding.scope, row["message_id"], row["text"])
            starts = {k: Cursor(*v) for k, v in json.loads(row["baselines"]).items()}
            matches, seen = {}, {}
            for record in records:
                if not isinstance(record, SourceRecord):
                    raise SocialError("invalid_echo_record")
                record.verify_binding(binding, record.shard)
                if record.message_id in seen and seen[record.message_id] != record.fingerprint:
                    raise SocialError("echo_source_identity_conflict")
                seen[record.message_id] = record.fingerprint
                if record.shard not in starts:
                    raise SocialError("echo_shard_without_baseline")
                if (record.position > starts[record.shard] and record.message_type == "text"
                        and record.sender_id == binding.account.self_id and record.text == action.text):
                    matches[record.message_id] = record
            if len(matches) != 1:
                raise SocialError("local_echo_not_unique")
            match = next(iter(matches.values()))
            evidence = DeliveryEvidence(digest([row["operation_id"], match.message_id]), action_id, binding.scope,
                binding.generation, "local_echo", row["operation_id"], row["content_sha256"], match.message_id)
        return BridgeStore(self.ledger).record_evidence(evidence)
