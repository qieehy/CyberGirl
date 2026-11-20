"""Parent-owned workbench state. No native effects, HTTP or model calls."""

import hashlib
from datetime import datetime, timedelta, timezone

from ..bridges.wechat.background_text import require_background_bmp_text
from ..bridges.wechat.contracts import digest
from ..bridges.wechat.store import BridgeStore
from ..social.models import SocialError, identifier, text_value
from .preferences import read_preferences


def migrate(db):
    db.execute("""CREATE TABLE bridge_message_payloads(
        message_id TEXT PRIMARY KEY REFERENCES bridge_records(message_id),scope TEXT NOT NULL,
        text TEXT NOT NULL,sender_id TEXT NOT NULL,occurred_at REAL NOT NULL,
        received_at REAL NOT NULL,message_type TEXT NOT NULL)""")
    db.execute("""CREATE TABLE workbench_context(
        scope TEXT PRIMARY KEY REFERENCES scopes(scope),revision INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE workbench_drafts(
        draft_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,
        source_message_id TEXT UNIQUE NOT NULL REFERENCES bridge_message_payloads(message_id),
        scope TEXT NOT NULL,generation TEXT NOT NULL,message_revision INTEGER NOT NULL,
        persona_version INTEGER NOT NULL,policy_revision INTEGER NOT NULL,model TEXT NOT NULL,
        started_at REAL NOT NULL,expires_at REAL NOT NULL,state TEXT NOT NULL,reason TEXT,
        action_id TEXT UNIQUE REFERENCES outbox(action_id),model_calls INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE workbench_draft_revisions(
        draft_id TEXT NOT NULL REFERENCES workbench_drafts(draft_id),version INTEGER NOT NULL,
        text TEXT NOT NULL,sha256 TEXT NOT NULL,origin TEXT NOT NULL,created_at REAL NOT NULL,
        PRIMARY KEY(draft_id,version))""")
    db.execute("""CREATE TABLE workbench_requests(
        request_id TEXT PRIMARY KEY,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,
        draft_id TEXT NOT NULL REFERENCES workbench_drafts(draft_id),state TEXT NOT NULL,
        payload TEXT NOT NULL,created_at REAL NOT NULL)""")
    db.execute("PRAGMA user_version=9")


def ingest_payload(db, binding, record):
    """Called inside the same transaction as source record and cursor ingestion."""
    scope = binding.scope.key()
    db.execute("INSERT INTO bridge_message_payloads VALUES(?,?,?,?,?,?,?)",
               (record.message_id, scope, record.text, record.sender_id, record.occurred_at,
                record.received_at, record.message_type))
    if record.message_type in {"text", "image", "sticker", "voice"}:
        db.execute("""INSERT INTO workbench_context VALUES(?,1)
            ON CONFLICT(scope) DO UPDATE SET revision=revision+1""", (scope,))
        db.execute("UPDATE workbench_drafts SET state='CANCELLED',reason='new_message' WHERE scope=? AND state='READY'", (scope,))
        db.execute("""UPDATE outbox SET state='CANCELLED',reason='new_message' WHERE state='QUEUED'
            AND action_id IN (SELECT action_id FROM workbench_drafts WHERE scope=?)""", (scope,))


class WorkbenchState:
    def __init__(self, ledger, binding):
        self.ledger, self.binding = ledger, binding

    def revision(self, db=None):
        row = (db or self.ledger._db).execute("SELECT revision FROM workbench_context WHERE scope=?",
                                             (self.binding.scope.key(),)).fetchone()
        return row[0] if row else 0

    def _draft(self, db, draft_id):
        identifier(draft_id)
        row = db.execute("SELECT * FROM workbench_drafts WHERE draft_id=? AND scope=?",
                         (draft_id, self.binding.scope.key())).fetchone()
        if row is None:
            raise SocialError("not_found")
        return row

    def valid(self, db, row):
        control = db.execute("SELECT * FROM control").fetchone()
        bound = db.execute("SELECT * FROM bridge_bindings WHERE scope=?", (row["scope"],)).fetchone()
        return (row["generation"] == self.binding.generation and bound and bound["active"]
                and not bound["blocked_reason"] and bound["generation"] == row["generation"]
                and not control["paused"] and control["revision"] == row["policy_revision"]
                and control["persona_version"] == row["persona_version"]
                and self.revision(db) == row["message_revision"] and self.ledger.now() < row["expires_at"])

    def require_ready(self, db, draft_id, version=None):
        row = self._draft(db, draft_id)
        if row["state"] != "READY" or not self.valid(db, row):
            raise SocialError("stale_draft")
        revision = db.execute("SELECT * FROM workbench_draft_revisions WHERE draft_id=? ORDER BY version DESC LIMIT 1",
                              (draft_id,)).fetchone()
        if revision is None or (version is not None and (type(version) is not int or revision["version"] != version)):
            raise SocialError("version_conflict")
        return row, revision

    def draft(self, draft_id):
        with self.ledger._transaction() as db:
            row = self._draft(db, draft_id)
            if row["state"] == "READY" and not self.valid(db, row):
                db.execute("UPDATE workbench_drafts SET state='CANCELLED',reason='stale_draft' WHERE draft_id=?", (draft_id,))
                row = self._draft(db, draft_id)
            result = dict(row)
            revision = db.execute("SELECT * FROM workbench_draft_revisions WHERE draft_id=? ORDER BY version DESC LIMIT 1",
                                  (draft_id,)).fetchone()
            result.update(version=revision["version"] if revision else 0, text=revision["text"] if revision else None,
                          content_sha256=revision["sha256"] if revision else None)
            if revision:
                try:
                    require_background_bmp_text(revision["text"])
                    if len(revision['text']) > read_preferences(db, self.binding.scope.key())['config']['reply_max_chars']:
                        raise SocialError('reply_configured_limit')
                    result["send_error"] = None
                except SocialError as exc:
                    result["send_error"] = exc.code
            if row["action_id"]:
                result["delivery"] = dict(db.execute("SELECT state,reason FROM outbox WHERE action_id=?", (row["action_id"],)).fetchone())
                result["local_echo"] = bool(db.execute("SELECT 1 FROM delivery_evidence WHERE action_id=? AND kind='local_echo'", (row["action_id"],)).fetchone())
            trace = db.execute("SELECT payload FROM workbench_requests WHERE draft_id=? AND kind='model_trace'", (draft_id,)).fetchone()
            if trace:
                import json

                result["model_trace"] = json.loads(trace[0])
            trace = db.execute("SELECT payload FROM workbench_requests WHERE draft_id=? AND kind='rule_trace'", (draft_id,)).fetchone()
            if trace:
                import json
                result['rule_trace'] = json.loads(trace[0])
            return result

    def snapshot(self):
        db = self.ledger._db
        messages = db.execute("""SELECT b.message_id,b.disposition,p.text,p.sender_id,p.occurred_at,p.received_at,p.message_type
            FROM bridge_records b JOIN bridge_cursors c USING(source_key)
            LEFT JOIN bridge_message_payloads p USING(message_id) WHERE c.scope=? ORDER BY b.rowid DESC LIMIT 100""",
                              (self.binding.scope.key(),)).fetchall()
        ids = [r[0] for r in db.execute("SELECT draft_id FROM workbench_drafts WHERE scope=? ORDER BY started_at DESC LIMIT 100",
                                      (self.binding.scope.key(),))]
        saved = self.media_results(db)
        rendered = []
        for row in messages:
            item = dict(row)
            if item["message_id"] in saved:
                item["media"] = saved[item["message_id"]]
                item["recognized_text"] = item["media"]["text"]
            rendered.append(item)
        return dict(messages=rendered, message_revision=self.revision(),
                    drafts=[self.draft(i) for i in ids])

    def media_results(self, db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_media_results'").fetchone():
            return {}
        return {r["message_id"]: dict(r) for r in db.execute('''SELECT m.* FROM workbench_media_results m
            JOIN bridge_message_payloads p USING(message_id) WHERE p.scope=?''', (self.binding.scope.key(),))}

    def begin_generation(self, request_id, source_message_id, expected_message_revision, expected_persona_version, model, *, private=False):
        identifier(request_id)
        identifier(source_message_id)
        if type(expected_message_revision) is not int or type(expected_persona_version) is not int:
            raise SocialError("invalid_request")
        fingerprint = digest([source_message_id, expected_message_revision, expected_persona_version, model])
        with self.ledger._transaction() as db:
            old = db.execute("SELECT * FROM workbench_drafts WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint or old["scope"] != self.binding.scope.key():
                    raise SocialError("event_identity_conflict")
                return old["draft_id"], False
            if db.execute("SELECT 1 FROM workbench_drafts WHERE source_message_id=?", (source_message_id,)).fetchone():
                raise SocialError("source_generation_already_used")
            if db.execute("SELECT 1 FROM workbench_drafts WHERE state='RUNNING'").fetchone():
                raise SocialError("busy")
            BridgeStore._binding(db, self.binding)
            source = db.execute("SELECT * FROM bridge_message_payloads WHERE message_id=? AND scope=? AND message_type IN ('text','image','sticker','voice')",
                                (source_message_id, self.binding.scope.key())).fetchone()
            if source is None:
                raise SocialError("not_found")
            if private:
                self._private_source(db, source_message_id)
            control = db.execute("SELECT * FROM control").fetchone()
            if control["paused"] or self.revision(db) != expected_message_revision or control["persona_version"] != expected_persona_version:
                raise SocialError("version_conflict")
            now = self.ledger.now()
            zone = timezone(timedelta(hours=8))
            midnight = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            budget = read_preferences(db, self.binding.scope.key())['config']['daily_budget']
            from .companion import daily_calls
            if model != 'local-rule' and daily_calls(db, now) >= budget:
                raise SocialError("model_daily_budget")
            draft_id = digest([self.binding.scope.key(), request_id, "workbench-draft"])
            db.execute("""INSERT INTO workbench_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'RUNNING',NULL,NULL,0)""",
                       (draft_id, request_id, fingerprint, source_message_id, self.binding.scope.key(), self.binding.generation,
                        expected_message_revision, expected_persona_version, control["revision"], model, now, now + 120))
            return draft_id, True

    def compose_private(self, request_id, source_message_id, expected_message_revision, expected_persona_version, text):
        """Local operator text only; never schedules inference or creates an outbox."""
        identifier(request_id)
        identifier(source_message_id)
        text_value(text, 2000)
        if type(expected_message_revision) is not int or type(expected_persona_version) is not int:
            raise SocialError("invalid_request")
        fingerprint = digest(["local-operator", source_message_id, expected_message_revision, expected_persona_version, text])
        with self.ledger._transaction() as db:
            old = db.execute("SELECT * FROM workbench_drafts WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint or old["scope"] != self.binding.scope.key():
                    raise SocialError("event_identity_conflict")
                return old["draft_id"]
            if db.execute("SELECT 1 FROM workbench_drafts WHERE source_message_id=?", (source_message_id,)).fetchone():
                raise SocialError("source_generation_already_used")
            self._private_source(db, source_message_id)
            now = self.ledger.now()
            control = db.execute("SELECT * FROM control").fetchone()
            if control["paused"] or self.revision(db) != expected_message_revision or control["persona_version"] != expected_persona_version:
                raise SocialError("version_conflict")
            draft_id = digest([self.binding.scope.key(), request_id, "workbench-local-draft"])
            db.execute("INSERT INTO workbench_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'READY',NULL,NULL,0)",
                (draft_id, request_id, fingerprint, source_message_id, self.binding.scope.key(), self.binding.generation,
                 expected_message_revision, expected_persona_version, control["revision"], "local-operator", now, now + 120))
            self._revision(db, draft_id, text, "operator")
            return draft_id

    def _private_source(self, db, source_message_id):
        bound = BridgeStore._binding(db, self.binding)
        if (bound["mode"] != "observe" or self.binding.kind != "dm"
                or self.binding.username in {"filehelper", self.binding.account.self_id}):
            raise SocialError("private_local_draft_scope_required")
        source = db.execute("""SELECT p.*,b.disposition,c.db_epoch,i.source_verified,i.is_self
                FROM bridge_message_payloads p
                JOIN bridge_records b USING(message_id) JOIN bridge_cursors c USING(source_key)
                LEFT JOIN inbox i ON i.event_id=b.event_id
                WHERE p.message_id=? AND p.scope=? AND c.scope=?""",
                (source_message_id, self.binding.scope.key(), self.binding.scope.key())).fetchone()
        if (source is None or source["disposition"] not in {"OBSERVED", "stale_event"} or source["message_type"] not in {"text", "image", "sticker", "voice"}
                    or source["sender_id"] != self.binding.username or source["db_epoch"] != self.binding.account.db_epoch
                    or (source["disposition"] == "OBSERVED" and (source["source_verified"] != 1 or source["is_self"] != 0))):
            raise SocialError("private_peer_source_required")
        return source

    def preview_private(self, draft_id, expected_version, *, model=None):
        """A read-only rendering of a saved revision, with no confirmation token."""
        with self.ledger._transaction() as db:
            draft, revision = self.require_ready(db, draft_id, expected_version)
            local = draft["model"] == "local-operator" and draft["model_calls"] == 0
            generated = model is not None and draft["model"] == model and draft["model_calls"] == 1
            rule = draft['model'] == 'local-rule' and draft['model_calls'] == 0 and db.execute("SELECT 1 FROM workbench_requests WHERE draft_id=? AND kind='rule_trace'", (draft_id,)).fetchone()
            if not (local or generated or rule):
                raise SocialError("private_local_draft_scope_required")
            require_background_bmp_text(revision["text"])
            source = db.execute("SELECT p.occurred_at,p.received_at,b.disposition FROM bridge_message_payloads p "
                                "JOIN bridge_records b USING(message_id) WHERE p.message_id=?", (draft["source_message_id"],)).fetchone()
            return dict(preview_only=True, send_enabled=False, draft_id=draft_id, version=revision["version"],
                        source_disposition=source["disposition"], source_occurred_at=source["occurred_at"],
                        source_received_at=source["received_at"],
                        source_message_id=draft["source_message_id"], account_id=self.binding.account.account_id,
                        target=self.binding.username, generation=draft["generation"], text=revision["text"],
                        content_sha256=revision["sha256"], expires_at=draft["expires_at"])

    def context(self, draft_id):
        with self.ledger._transaction() as db:
            row = self._draft(db, draft_id)
            if row["state"] != "RUNNING" or not self.valid(db, row):
                raise SocialError("stale_draft")
            text = db.execute("SELECT text FROM bridge_message_payloads WHERE message_id=?", (row["source_message_id"],)).fetchone()[0]
            media = self.media_results(db)
            text = media.get(row["source_message_id"], {}).get("text") or text
            rows = db.execute("""SELECT p.message_id,p.text AS user,o.text AS assistant FROM workbench_drafts d
                JOIN bridge_message_payloads p ON p.message_id=d.source_message_id JOIN outbox o USING(action_id)
                WHERE d.scope=? AND (o.state='CONFIRMED' OR (d.request_id LIKE 'auto:%' AND EXISTS(
                    SELECT 1 FROM delivery_evidence e WHERE e.action_id=o.action_id AND e.kind='local_echo')))
                ORDER BY d.started_at DESC LIMIT ?""", (row["scope"], read_preferences(db, row['scope'])['config']['context_pairs'])).fetchall()
            history, size = [], 0
            for item in rows:
                user = media.get(item["message_id"], {}).get("text") or item["user"]
                size += len(user) + len(item["assistant"])
                if size > 10000:
                    break
                history.append(dict(user=user, assistant=item["assistant"]))
            return text, list(reversed(history)), self.ledger._persona(db, row["persona_version"])

    def finish_generation(self, draft_id, decision=None, *, reason=None, unknown=False, origin='model'):
        with self.ledger._transaction() as db:
            row = self._draft(db, draft_id)
            if row["state"] != "RUNNING":
                return
            state = "UNKNOWN" if unknown else "FAILED"
            if not unknown and not self.valid(db, row):
                state, reason = "CANCELLED", "stale_draft"
            elif decision is not None:
                if decision.action.value == "SILENT":
                    state = "SILENT"
                else:
                    state = "READY"
                    self._revision(db, draft_id, decision.reply_text, origin)
            db.execute("UPDATE workbench_drafts SET state=?,reason=? WHERE draft_id=?", (state, reason, draft_id))

    def _revision(self, db, draft_id, text, origin):
        text_value(text, 2000)
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM workbench_draft_revisions WHERE draft_id=?", (draft_id,)).fetchone()[0]
        db.execute("INSERT INTO workbench_draft_revisions VALUES(?,?,?,?,?,?)",
                   (draft_id, version, text, hashlib.sha256(text.encode()).hexdigest(), origin, self.ledger.now()))

    def request(self, db, request_id, kind, fingerprint):
        identifier(request_id)
        old = db.execute("SELECT * FROM workbench_requests WHERE request_id=?", (request_id,)).fetchone()
        if old and (old["kind"] != kind or old["fingerprint"] != fingerprint):
            raise SocialError("event_identity_conflict")
        return old

    def edit(self, draft_id, request_id, expected_version, text):
        text_value(text, 2000)
        fingerprint = digest([draft_id, expected_version, text])
        with self.ledger._transaction() as db:
            if not self.request(db, request_id, "edit", fingerprint):
                self.require_ready(db, draft_id, expected_version)
                self._revision(db, draft_id, text, "operator")
                db.execute("INSERT INTO workbench_requests VALUES(?,?,?,?,'DONE','{}',?)",
                           (request_id, "edit", fingerprint, draft_id, self.ledger.now()))
                db.execute("UPDATE workbench_requests SET state='CANCELLED' WHERE draft_id=? AND kind='prepare' AND state='READY'", (draft_id,))
        return self.draft(draft_id)

    def cancel(self, draft_id):
        with self.ledger._transaction() as db:
            row = self._draft(db, draft_id)
            if row["action_id"] is not None:
                raise SocialError("draft_already_submitted")
            db.execute("UPDATE workbench_drafts SET state='CANCELLED',reason='operator_cancelled' WHERE draft_id=? AND state IN ('READY','RUNNING')", (draft_id,))
        return self.draft(draft_id)
