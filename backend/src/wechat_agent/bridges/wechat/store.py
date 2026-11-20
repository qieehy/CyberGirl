"""WeChat state in the existing Ledger's connection and owner transaction.

No network, model, native UI, or source database access lives in this module.
Only return an ingestion result to the reader after the transaction commits.
"""

import hashlib
import json
from dataclasses import asdict

from ...social.models import SocialError, identifier, timestamp
from .contracts import ConversationBinding, Cursor, DeliveryEvidence, SourceRecord, digest


def migrate(db):
    """Called inside the Ledger migration transaction, after its backup."""
    db.execute("""CREATE TABLE bridge_bindings(
        scope TEXT PRIMARY KEY REFERENCES scopes(scope),account_id TEXT NOT NULL,
        account_generation TEXT NOT NULL,generation TEXT NOT NULL,payload TEXT NOT NULL,
        mode TEXT NOT NULL,active INTEGER NOT NULL,blocked_reason TEXT)""")
    db.execute("""CREATE TABLE bridge_cursors(
        source_key TEXT PRIMARY KEY,scope TEXT NOT NULL REFERENCES bridge_bindings(scope),
        db_epoch TEXT NOT NULL,shard TEXT NOT NULL,sort_seq INTEGER NOT NULL,local_id INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE bridge_records(
        message_id TEXT PRIMARY KEY,source_key TEXT NOT NULL REFERENCES bridge_cursors(source_key),
        fingerprint TEXT NOT NULL,sort_seq INTEGER NOT NULL,local_id INTEGER NOT NULL,
        disposition TEXT NOT NULL,event_id TEXT REFERENCES inbox(event_id))""")
    db.execute("""CREATE TABLE delivery_evidence(
        evidence_id TEXT PRIMARY KEY,action_id TEXT NOT NULL REFERENCES outbox(action_id),
        fingerprint TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,recorded_at REAL NOT NULL)""")
    db.execute("ALTER TABLE inbox ADD COLUMN bridge_generation TEXT")
    db.execute("ALTER TABLE outbox ADD COLUMN platform_message_id TEXT")
    for row in db.execute("SELECT scope FROM scopes").fetchall():
        if json.loads(row[0])[0] == "fake":
            db.execute("""UPDATE outbox SET platform_message_id=receipt_id WHERE state='CONFIRMED'
                          AND event_id IN (SELECT event_id FROM inbox WHERE scope=?)""", (row[0],))
    db.execute("PRAGMA user_version=5")


class BridgeStore:
    """Trusted parent-process API; never expose binding/evidence constructors to LLM input."""

    def __init__(self, ledger):
        self.ledger = ledger

    def bind(self, binding: ConversationBinding, *, mode="observe"):
        if not isinstance(binding, ConversationBinding) or mode not in {"observe", "draft"}:
            raise SocialError("invalid_bridge_binding")
        if mode == "draft" and binding.kind != "dm":
            raise SocialError("wechat_group_drafts_unavailable")
        scope = binding.scope.key()
        with self.ledger._transaction() as db:
            if db.execute("SELECT 1 FROM bridge_bindings WHERE active=1 AND account_id!=?",
                          (binding.account.account_id,)).fetchone():
                raise SocialError("single_wechat_account_only")
            old = db.execute("SELECT * FROM bridge_bindings WHERE scope=?", (scope,)).fetchone()
            if old and old["blocked_reason"]:
                raise SocialError("source_requires_review")
            if not old and db.execute("SELECT COUNT(*) FROM bridge_bindings").fetchone()[0] >= 1000:
                raise SocialError("bridge_capacity")
            # Account/window changes fence all old conversations, not just this one.
            stale = db.execute("SELECT scope FROM bridge_bindings WHERE account_id=? AND account_generation!=?",
                               (binding.account.account_id, binding.account.generation)).fetchall()
            for row in stale:
                self._deactivate(db, row[0], "binding_changed")
            if old and (not old["active"] or old["generation"] != binding.generation or old["mode"] != mode):
                self._deactivate(db, scope, "binding_changed")
            db.execute("INSERT OR IGNORE INTO scopes(scope,owner,enabled,revision) VALUES(?,?,0,1)",
                       (scope, binding.account.self_id))
            db.execute("""INSERT INTO bridge_bindings VALUES(?,?,?,?,?,?,1,NULL)
                ON CONFLICT(scope) DO UPDATE SET account_generation=excluded.account_generation,
                generation=excluded.generation,payload=excluded.payload,mode=excluded.mode,active=1""",
                       (scope, binding.account.account_id, binding.account.generation, binding.generation,
                        json.dumps(asdict(binding), ensure_ascii=False, sort_keys=True), mode))

    def _deactivate(self, db, scope, reason):
        db.execute("UPDATE bridge_bindings SET active=0 WHERE scope=?", (scope,))
        self.ledger._cancel_queued(db, scope, reason)
        db.execute("UPDATE inbox SET state='CANCELLED',reason=? WHERE scope=? AND state IN ('RECEIVED','PROCESSING','BUFFERED')",
                   (reason, scope))

    def unbind(self, binding):
        with self.ledger._transaction() as db:
            self._binding(db, binding)
            self._deactivate(db, binding.scope.key(), "binding_revoked")

    @staticmethod
    def _binding(db, binding):
        row = db.execute("SELECT * FROM bridge_bindings WHERE scope=?", (binding.scope.key(),)).fetchone()
        if not row or not row["active"] or row["blocked_reason"] or row["generation"] != binding.generation:
            raise SocialError("inactive_bridge_binding")
        return row

    def baseline(self, binding, shard, position: Cursor, *, local_fence=None):
        """Explicit new-source baseline only. Existing cursors cannot be skipped/reset."""
        if not isinstance(position, Cursor):
            raise SocialError("invalid_cursor")
        if local_fence is None:
            local_fence = position.local_id
        if type(local_fence) is not int or not position.local_id <= local_fence <= 2**63 - 1:
            raise SocialError("invalid_local_fence")
        key = binding.source_key(shard)
        with self.ledger._transaction() as db:
            self._binding(db, binding)
            row = db.execute("SELECT * FROM bridge_cursors WHERE source_key=?", (key,)).fetchone()
            if row:
                return Cursor(row["sort_seq"], row["local_id"])
            if db.execute("SELECT COUNT(*) FROM bridge_cursors").fetchone()[0] >= 1000:
                raise SocialError("bridge_capacity")
            db.execute("INSERT INTO bridge_cursors VALUES(?,?,?,?,?,?,?)",
                       (key, binding.scope.key(), binding.account.db_epoch, shard, position.sort_seq, position.local_id, local_fence))
            return position

    def cursor(self, binding, shard):
        with self.ledger._transaction() as db:
            self._binding(db, binding)
            row = db.execute("SELECT * FROM bridge_cursors WHERE source_key=?", (binding.source_key(shard),)).fetchone()
            if row is None:
                raise SocialError("baseline_required")
            return Cursor(row["sort_seq"], row["local_id"])

    def ingest(self, binding, shard, start: Cursor, records: tuple[SourceRecord, ...]):
        if not isinstance(start, Cursor) or type(records) is not tuple or not 1 <= len(records) <= 100:
            raise SocialError("invalid_source_batch")
        previous = start
        for record in records:
            if not isinstance(record, SourceRecord):
                raise SocialError("invalid_source_record")
            record.verify_binding(binding, shard)
            if record.position <= previous:
                raise SocialError("unordered_source_batch")
            previous = record.position
        now = self.ledger.now()
        timestamp(now)
        try:
            with self.ledger._transaction() as db:
                grant = self._binding(db, binding)
                if db.execute("SELECT paused FROM control WHERE id=1").fetchone()[0]:
                    raise SocialError("bridge_paused")
                key = binding.source_key(shard)
                row = db.execute("SELECT * FROM bridge_cursors WHERE source_key=?", (key,)).fetchone()
                if row is None:
                    raise SocialError("baseline_required")
                current = Cursor(row["sort_seq"], row["local_id"])
                replay = start < current and records[-1].position <= current
                if start != current and not replay:
                    raise SocialError("cursor_mismatch")
                results = []
                for record in records:
                    old = db.execute("SELECT * FROM bridge_records WHERE message_id=?", (record.message_id,)).fetchone()
                    if old:
                        if old["fingerprint"] != record.fingerprint or old["source_key"] != key:
                            raise SocialError("event_identity_conflict")
                        results.append({"message_id": record.message_id, "disposition": old["disposition"],
                                        "event_id": old["event_id"], "fresh": False})
                        continue
                    if replay:
                        raise SocialError("unrecorded_cursor_replay")
                    if db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] >= 10000:
                        raise SocialError("bridge_capacity")
                    event_id = None
                    if record.message_type not in {"text", "image", "sticker", "voice"}:
                        disposition = "unsupported_message_type"
                    elif record.sender_id == binding.account.self_id:
                        disposition = "self_echo"
                    else:
                        event = record.event(binding)
                        result, fresh = self.ledger._accept(db, event, now, observe_only=grant["mode"] == "observe")
                        disposition = result["reason"] if result["state"] == "REJECTED" else result["state"]
                        if result["state"] != "REJECTED":
                            event_id = event.key
                            if fresh:
                                db.execute("UPDATE inbox SET bridge_generation=?,reply_to=? WHERE event_id=?",
                                           (binding.generation, event.reply_to, event_id))
                    db.execute("INSERT INTO bridge_records VALUES(?,?,?,?,?,?,?)",
                               (record.message_id, key, record.fingerprint, record.sort_seq, record.local_id,
                                disposition, event_id))
                    from ...workbench.state import ingest_payload

                    ingest_payload(db, binding, record)
                    results.append({"message_id": record.message_id, "disposition": disposition,
                                    "event_id": event_id, "fresh": True})
                end = max(current, records[-1].position)
                db.execute("UPDATE bridge_cursors SET sort_seq=?,local_id=?,local_fence=MAX(local_fence,?) WHERE source_key=?",
                           (end.sort_seq, end.local_id, max(r.local_id for r in records), key))
            # Transaction exit/commit occurs BEFORE the caller can ACK this result.
            return {"cursor": end, "records": results}
        except SocialError as exc:
            if exc.code == "event_identity_conflict":
                # The failed batch rolled back. Persist a stop independently.
                with self.ledger._transaction() as db:
                    self._deactivate(db, binding.scope.key(), "event_identity_conflict")
                    db.execute("UPDATE bridge_bindings SET blocked_reason='event_identity_conflict' WHERE scope=?",
                               (binding.scope.key(),))
            raise

    def record_evidence(self, evidence: DeliveryEvidence):
        """Append evidence; only explicit manual confirmation reconciles UNKNOWN.

        The trusted operator invokes manual_confirmation after checking the result.
        No UI/LLM endpoint or automatic delivery-signal claim is added here.
        """
        if not isinstance(evidence, DeliveryEvidence):
            raise SocialError("invalid_delivery_evidence")
        payload = asdict(evidence)
        fingerprint = digest(payload)
        with self.ledger._transaction() as db:
            old = db.execute("SELECT fingerprint FROM delivery_evidence WHERE evidence_id=?", (evidence.evidence_id,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise SocialError("evidence_identity_conflict")
                return False
            row = db.execute("""SELECT o.*,i.scope,i.bridge_generation,a.state AS attempt_state
                FROM outbox o JOIN inbox i USING(event_id) JOIN attempts a USING(action_id)
                WHERE o.action_id=?""", (evidence.action_id,)).fetchone()
            if (not row or row["scope"] != evidence.scope.key() or row["bridge_generation"] != evidence.binding_generation
                    or hashlib.sha256(row["text"].encode()).hexdigest() != evidence.content_sha256
                    or row["state"] != "UNCONFIRMED" or row["attempt_state"] != "UNCONFIRMED"):
                raise SocialError("evidence_action_mismatch")
            if db.execute("SELECT COUNT(*) FROM delivery_evidence").fetchone()[0] >= 10000:
                raise SocialError("evidence_capacity")
            # Conflicting native message IDs cannot overwrite an earlier echo.
            if (row["platform_message_id"] is not None and evidence.platform_message_id is not None
                    and row["platform_message_id"] != evidence.platform_message_id):
                raise SocialError("evidence_message_mismatch")
            db.execute("INSERT INTO delivery_evidence VALUES(?,?,?,?,?,?)",
                       (evidence.evidence_id, evidence.action_id, fingerprint, evidence.kind,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True), self.ledger.now()))
            if evidence.platform_message_id is not None:
                db.execute("UPDATE outbox SET platform_message_id=? WHERE action_id=?",
                           (evidence.platform_message_id, evidence.action_id))
            if evidence.kind == "manual_confirmation":
                identifier(evidence.reference)
                db.execute("UPDATE outbox SET state='CONFIRMED',reason='manual_confirmation',receipt_id=? WHERE action_id=?",
                           (evidence.reference, evidence.action_id))
                # Preserve the actual attempt's uncertainty; reconciliation is evidence, not a resend.
            return True
