"""Single-owner SQLite inbox/outbox with durable submission uncertainty."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..context import ConversationScope
from ..providers.codex.protection import WindowsOwner
from .models import (
    ActionKind,
    Decision,
    IncomingEvent,
    OutgoingAction,
    SocialError,
    identifier,
    timestamp,
)
from .persona import Persona, assemble

APPLICATION_ID = 0x57435331

SCHEMA = """
CREATE TABLE control(id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL, revision INTEGER NOT NULL);
INSERT INTO control VALUES(1,0,1);
CREATE TABLE scopes(scope TEXT PRIMARY KEY, owner TEXT NOT NULL, enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL, context_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE inbox(seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, scope TEXT NOT NULL REFERENCES scopes(scope),
                   message_id TEXT NOT NULL, fingerprint TEXT NOT NULL, text TEXT NOT NULL,
                   sender_id TEXT NOT NULL, occurred_at REAL NOT NULL, source_verified INTEGER NOT NULL,
                   is_self INTEGER NOT NULL, mention_self INTEGER NOT NULL,
                   received_at REAL NOT NULL, policy_revision INTEGER NOT NULL, global_revision INTEGER NOT NULL,
                   context_version INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT);
CREATE TABLE decisions(event_id TEXT PRIMARY KEY REFERENCES inbox(event_id), action TEXT NOT NULL);
CREATE TABLE outbox(action_id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL REFERENCES inbox(event_id),
                    text TEXT NOT NULL, state TEXT NOT NULL, expires_at REAL NOT NULL, reason TEXT, receipt_id TEXT);
CREATE TABLE attempts(action_id TEXT PRIMARY KEY REFERENCES outbox(action_id), started_at REAL NOT NULL,
                      state TEXT NOT NULL, finished_at REAL);
CREATE INDEX inbox_scope ON inbox(scope,seq);
PRAGMA user_version=1;
"""


class Ledger:
    """Use as a context manager. Only one process may own a database at a time.

    Transactions never span awaits or external I/O. The Windows lease prevents
    another process from treating a live sender's SENDING row as a crashed one.
    """

    def __init__(self, path: Path, *, now=time.time):
        self.path, self.now = path.resolve(), now
        self._owner, self._db = WindowsOwner(), None

    def __enter__(self):
        if self._db is not None:
            raise SocialError("already_open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner.acquire(self.path.with_name(self.path.name + ".owner.lock"))
        try:
            self._db = sqlite3.connect(self.path, isolation_level=None, timeout=1)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA synchronous=FULL")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            application_id = self._db.execute("PRAGMA application_id").fetchone()[0]
            if version == 0:
                if application_id != 0 or self._db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                    raise SocialError("foreign_database")
                self._db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA
                                       + f"\nPRAGMA application_id={APPLICATION_ID};\nCOMMIT;")
            elif version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10} or application_id != APPLICATION_ID:
                raise SocialError("unsupported_schema")
            if version in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
                backup_path = self.path.with_name(self.path.name + f".v{version}-{time.time_ns()}.bak")
                with backup_path.open("xb"):
                    pass
                backup = sqlite3.connect(backup_path)
                try:
                    self._db.backup(backup)
                finally:
                    backup.close()
            if version in {0, 1}:
                with self._transaction() as db:
                    db.execute("CREATE TABLE personas(version INTEGER PRIMARY KEY,name TEXT NOT NULL,style TEXT NOT NULL,notes TEXT NOT NULL,created_at REAL NOT NULL)")
                    default = Persona()
                    db.execute("INSERT INTO personas VALUES(?,?,?,?,?)", (1, default.name, default.style, default.notes, self.now()))
                    db.execute("ALTER TABLE control ADD COLUMN persona_version INTEGER NOT NULL DEFAULT 1")
                    # NULL distinguishes old S1 records from a reproducible S2 input.
                    db.execute("ALTER TABLE inbox ADD COLUMN persona_version INTEGER")
                    db.execute("ALTER TABLE inbox ADD COLUMN context_snapshot TEXT")
                    db.execute("ALTER TABLE inbox ADD COLUMN prompt_sha256 TEXT")
                    db.execute("PRAGMA user_version=2")
            if version in {0, 1, 2}:
                with self._transaction() as db:
                    db.execute("""CREATE TABLE previews(request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
                               persona_version INTEGER NOT NULL,policy_revision INTEGER NOT NULL,model TEXT NOT NULL,
                               input_text TEXT NOT NULL,prompt_sha256 TEXT NOT NULL,state TEXT NOT NULL,
                               started_at REAL NOT NULL,result TEXT NOT NULL)""")
                    db.execute("PRAGMA user_version=3")
            if version in {0, 1, 2, 3}:
                with self._transaction() as db:
                    db.execute("CREATE TABLE group_policies(scope TEXT PRIMARY KEY REFERENCES scopes(scope),bot_id TEXT NOT NULL,consent_ref TEXT NOT NULL)")
                    db.execute("CREATE TABLE group_runs(event_id TEXT PRIMARY KEY REFERENCES inbox(event_id),scope TEXT NOT NULL,started_at REAL NOT NULL)")
                    db.execute("ALTER TABLE inbox ADD COLUMN model_text TEXT")
                    db.execute("ALTER TABLE inbox ADD COLUMN reply_to TEXT")
                    db.execute("ALTER TABLE inbox ADD COLUMN batch_id TEXT")
                    db.execute("PRAGMA user_version=4")
            if version in {0, 1, 2, 3, 4}:
                from ..bridges.wechat.store import migrate

                with self._transaction() as db:
                    migrate(db)
            if version in {0, 1, 2, 3, 4, 5}:
                from ..bridges.wechat.send_state import migrate as migrate_sending

                with self._transaction() as db:
                    migrate_sending(db)
            if version in {0, 1, 2, 3, 4, 5, 6}:
                from ..bridges.wechat.receive_state import migrate as migrate_receiving

                with self._transaction() as db:
                    migrate_receiving(db)
            if version in {0, 1, 2, 3, 4, 5, 6, 7}:
                from ..bridges.wechat.channel_review import migrate as migrate_reviews

                with self._transaction() as db:
                    migrate_reviews(db)
            if version < 9:
                from ..workbench.state import migrate as migrate_workbench

                with self._transaction() as db:
                    migrate_workbench(db)
            if version < 10:
                from ..bridges.wechat.private_receive import migrate as migrate_private_receiving

                with self._transaction() as db:
                    migrate_private_receiving(db)
            with self._transaction() as db:
                from ..bridges.wechat.channel_review import recover as recover_reviews
                from ..bridges.wechat.send_state import recover

                recover(db)
                recover_reviews(db)
                # Persisted evidence is not a live verification of a new process.
                db.execute("UPDATE bridge_bindings SET active=0")
                # A process interruption cannot establish whether a remote call completed.
                db.execute("UPDATE previews SET state='UNKNOWN' WHERE state='RUNNING'")
                db.execute("UPDATE workbench_drafts SET state='UNKNOWN',reason='interrupted_generation' WHERE state='RUNNING'")
                db.execute("UPDATE workbench_drafts SET state='CANCELLED',reason='workbench_restarted' WHERE state='READY'")
                db.execute("UPDATE workbench_requests SET state='CANCELLED' WHERE kind='prepare' AND state='READY'")
                db.execute("UPDATE outbox SET state='UNCONFIRMED',reason='interrupted_submission' WHERE state='SENDING'")
                db.execute("UPDATE attempts SET state='UNCONFIRMED',finished_at=? WHERE state='SENDING'", (self.now(),))
                db.execute("UPDATE inbox SET state='CANCELLED',reason='interrupted_decision' WHERE state IN ('RECEIVED','PROCESSING','BUFFERED')")
                db.execute("UPDATE outbox SET state='EXPIRED',reason='draft_expired' WHERE state='QUEUED' AND expires_at<=?", (self.now(),))
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        self._owner.close()

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def _transaction(self):
        if self._db is None:
            raise SocialError("closed")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def configure_owner(self, scope: ConversationScope, owner: str, *, enabled: bool = True):
        identifier(owner)
        if scope.conversation_type != "dm" or type(enabled) is not bool:
            raise SocialError("unsupported_policy")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM scopes WHERE scope=?", (scope.key(),)).fetchone()
            if old and old["owner"] == owner and old["enabled"] == enabled:
                return
            db.execute("""INSERT INTO scopes(scope,owner,enabled,revision) VALUES(?,?,?,1)
                          ON CONFLICT(scope) DO UPDATE SET owner=excluded.owner,enabled=excluded.enabled,revision=revision+1""",
                       (scope.key(), owner, int(enabled)))
            self._cancel_queued(db, scope.key(), "policy_changed")

    def pause(self, paused: bool):
        if type(paused) is not bool:
            raise SocialError("invalid_policy")
        with self._transaction() as db:
            old = db.execute("SELECT paused FROM control WHERE id=1").fetchone()[0]
            if old == paused:
                return
            db.execute("UPDATE control SET paused=?,revision=revision+1 WHERE id=1", (int(paused),))
            db.execute("UPDATE outbox SET state='CANCELLED',reason='global_policy_changed' WHERE state='QUEUED'")

    @staticmethod
    def _cancel_queued(db, scope, reason):
        db.execute("""UPDATE outbox SET state='CANCELLED',reason=? WHERE state='QUEUED'
                      AND event_id IN (SELECT event_id FROM inbox WHERE scope=?)""", (reason, scope))

    def accept(self, event: IncomingEvent, *, buffer_group=False) -> tuple[dict, bool]:
        now = self.now()
        timestamp(now)
        with self._transaction() as db:
            return self._accept(db, event, now, buffer_group=buffer_group)

    def _accept(self, db, event, now, *, buffer_group=False, observe_only=False):
        """Internal admission shared by ordinary input and atomic bridge batches."""
        previous = db.execute("SELECT fingerprint FROM inbox WHERE event_id=?", (event.key,)).fetchone()
        if previous:
            if previous[0] != event.fingerprint:
                raise SocialError("event_identity_conflict")
            return self._summary(db, event.key), False
        grant = db.execute("SELECT * FROM scopes WHERE scope=?", (event.scope.key(),)).fetchone()
        control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
        is_group = event.scope.conversation_type == "group"
        if (not event.source_verified or event.is_self or not grant or control["paused"]
                or (not observe_only and (not grant["enabled"]
                    or (not is_group and (event.scope.conversation_type != "dm" or event.sender_id != grant["owner"]))))):
            return {"state": "REJECTED", "reason": "policy_denied", "action_id": None}, False
        if is_group and not observe_only:
            from .groups import group_admission

            reason = group_admission(db, event, buffer_group, now)
            if reason:
                return {"state": "REJECTED", "reason": reason, "action_id": None}, False
        if not now - 120 <= event.occurred_at <= event.received_at + 30 or not now - 120 <= event.received_at <= now + 30:
            return {"state": "REJECTED", "reason": "stale_event", "action_id": None}, False
        if db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] >= 10000:
            raise SocialError("ledger_capacity")
        version = grant["context_version"] + 1
        db.execute("UPDATE scopes SET context_version=? WHERE scope=?", (version, event.scope.key()))
        self._cancel_queued(db, event.scope.key(), "new_context")
        db.execute("""INSERT INTO inbox(event_id,scope,message_id,fingerprint,text,sender_id,occurred_at,
                      source_verified,is_self,mention_self,received_at,policy_revision,
                      global_revision,context_version,persona_version,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'RECEIVED')""",
                   (event.key, event.scope.key(), event.message_id, event.fingerprint, event.text,
                    event.sender_id, event.occurred_at, int(event.source_verified), int(event.is_self), int(event.mention_self), event.received_at,
                    grant["revision"], control["revision"], version, control["persona_version"]))
        if observe_only:
            db.execute("UPDATE inbox SET state='OBSERVED',reason='observe_only',reply_to=? WHERE event_id=?", (event.reply_to, event.key))
        elif is_group:
            db.execute("UPDATE inbox SET state='BUFFERED',reply_to=? WHERE event_id=?", (event.reply_to, event.key))
        return self._summary(db, event.key), True

    def _valid(self, db, row):
        grant = db.execute("SELECT * FROM scopes WHERE scope=?", (row["scope"],)).fetchone()
        control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
        if json.loads(row["scope"])[0] == "wechat":
            binding = db.execute("SELECT * FROM bridge_bindings WHERE scope=?", (row["scope"],)).fetchone()
            if (not binding or not binding["active"] or binding["blocked_reason"] or binding["mode"] != "draft"
                    or binding["generation"] != row["bridge_generation"]):
                return False
        return (grant["enabled"] and not control["paused"] and grant["revision"] == row["policy_revision"]
                and control["revision"] == row["global_revision"] and grant["context_version"] == row["context_version"])

    def start_decision(self, event_id: str) -> bool:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM inbox WHERE event_id=?", (event_id,)).fetchone()
            if not row or row["state"] != "RECEIVED":
                return False
            if not self._valid(db, row) or self.now() >= row["received_at"] + 120:
                db.execute("UPDATE inbox SET state='CANCELLED',reason='stale_context_or_policy' WHERE event_id=?", (event_id,))
                return False
            if db.execute("SELECT 1 FROM group_policies WHERE scope=?", (row["scope"],)).fetchone():
                from .groups import budget_reason

                reason = budget_reason(db, row["scope"], self.now(), include_calls=True)
                if reason:
                    db.execute("UPDATE inbox SET state='CANCELLED',reason=? WHERE event_id=?", (reason, event_id))
                    return False
                db.execute("INSERT INTO group_runs VALUES(?,?,?)", (event_id, row["scope"], self.now()))
            db.execute("UPDATE inbox SET state='PROCESSING' WHERE event_id=?", (event_id,))
            return True

    def finish_decision(self, event_id: str, decision: Decision):
        if not isinstance(decision, Decision):
            raise SocialError("invalid_decision")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM inbox WHERE event_id=?", (event_id,)).fetchone()
            if not row or row["state"] != "PROCESSING":
                raise SocialError("invalid_transition")
            if not self._valid(db, row) or self.now() >= row["received_at"] + 120:
                db.execute("UPDATE inbox SET state='CANCELLED',reason='stale_context_or_policy' WHERE event_id=?", (event_id,))
                return
            db.execute("INSERT INTO decisions VALUES(?,?)", (event_id, decision.action.value))
            db.execute("UPDATE inbox SET state='DECIDED' WHERE event_id=?", (event_id,))
            if decision.action == ActionKind.REPLY:
                action_id = hashlib.sha256((event_id + ":reply:0").encode()).hexdigest()
                db.execute("INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,?,?,'QUEUED',?)",
                           (action_id, event_id, decision.reply_text, row["received_at"] + 120))

    def fail_decision(self, event_id, *, cancelled=False):
        with self._transaction() as db:
            db.execute("UPDATE inbox SET state=?,reason='decision_interrupted' WHERE event_id=? AND state IN ('RECEIVED','PROCESSING')",
                       ("CANCELLED" if cancelled else "FAILED", event_id))

    @staticmethod
    def _summary(db, event_id):
        row = db.execute("""SELECT i.state AS input_state,o.state,o.action_id,COALESCE(o.reason,i.reason) AS reason,
                            d.action FROM inbox i LEFT JOIN outbox o USING(event_id)
                            LEFT JOIN decisions d USING(event_id) WHERE i.event_id=?""", (event_id,)).fetchone()
        if row is None:
            raise SocialError("unknown_event")
        return {"state": row["state"] or ("SILENT" if row["action"] == "SILENT" else row["input_state"]),
                "reason": row["reason"], "action_id": row["action_id"]}

    def summary(self, event_id):
        with self._transaction() as db:
            return self._summary(db, event_id)

    def action_summary(self, action_id):
        with self._transaction() as db:
            row = db.execute("SELECT state,reason,action_id FROM outbox WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise SocialError("unknown_action")
            return dict(row)

    def history(self, scope: ConversationScope):
        with self._transaction() as db:
            return self._history(db, scope.key())

    @staticmethod
    def _history(db, scope_key):
        rows = db.execute("""SELECT COALESCE(i.model_text,i.text) AS user,o.text AS assistant FROM inbox i JOIN outbox o USING(event_id)
                             WHERE i.scope=? AND i.origin='source' AND o.state='CONFIRMED' ORDER BY i.seq DESC LIMIT 16""", (scope_key,)).fetchall()
        history, size = [], 0
        for row in rows:
            size += len(row["user"]) + len(row["assistant"])
            if size > 10000:
                break
            history.append(dict(row))
        return list(reversed(history))

    @staticmethod
    def _persona(db, version):
        row = db.execute("SELECT version,name,style,notes FROM personas WHERE version=?", (version,)).fetchone()
        if row is None:
            raise SocialError("unknown_persona")
        return Persona(**dict(row))

    def current_persona(self):
        with self._transaction() as db:
            return self._persona(db, db.execute("SELECT persona_version FROM control WHERE id=1").fetchone()[0])

    def save_persona(self, expected_version: int, name: str, style: str, notes: str):
        if type(expected_version) is not int:
            raise SocialError("invalid_persona")
        with self._transaction() as db:
            current = db.execute("SELECT persona_version FROM control WHERE id=1").fetchone()[0]
            if current != expected_version:
                raise SocialError("version_conflict")
            if current >= 1000:
                raise SocialError("persona_capacity")
            persona = Persona(current + 1, name, style, notes)
            db.execute("INSERT INTO personas VALUES(?,?,?,?,?)", (persona.version, name, style, notes, self.now()))
            db.execute("UPDATE control SET persona_version=?,revision=revision+1 WHERE id=1", (persona.version,))
            db.execute("UPDATE outbox SET state='CANCELLED',reason='persona_changed' WHERE state='QUEUED'")
            return persona

    def decision_context(self, event_id):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM inbox WHERE event_id=?", (event_id,)).fetchone()
            if row is None or row["state"] != "PROCESSING" or not self._valid(db, row):
                raise SocialError("stale_context")
            persona = self._persona(db, row["persona_version"])
            history = self._history(db, row["scope"])
            _, user, digest = assemble(persona, row["model_text"] or row["text"], history)
            db.execute("UPDATE inbox SET context_snapshot=?,prompt_sha256=? WHERE event_id=?", (user, digest, event_id))
            return persona, history

    def console_snapshot(self, scope: ConversationScope):
        with self._transaction() as db:
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            rows = db.execute("""SELECT i.seq,i.event_id,i.text,i.sender_id,i.received_at,i.persona_version,i.prompt_sha256,i.batch_id,
                            i.state AS input_state,o.state AS delivery_state,o.action_id,o.text AS reply_text,
                            COALESCE(o.reason,i.reason) AS reason,d.action,a.started_at,a.finished_at,o.receipt_id
                            FROM inbox i LEFT JOIN outbox o USING(event_id) LEFT JOIN decisions d USING(event_id)
                            LEFT JOIN attempts a ON a.action_id=o.action_id WHERE i.scope=? ORDER BY i.seq DESC LIMIT 100""",
                              (scope.key(),)).fetchall()
            versions = db.execute("SELECT version,name,style,notes,created_at FROM personas ORDER BY version DESC LIMIT 30").fetchall()
            return {"persona": self._persona(db, control["persona_version"]).to_dict(), "paused": bool(control["paused"]),
                    "policy_version": control["revision"], "versions": [dict(r) for r in versions],
                    "events": [dict(r) for r in rows]}

    def recorded_event(self, scope: ConversationScope, event_id):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM inbox WHERE event_id=? AND scope=?", (event_id, scope.key())).fetchone()
            if row is None:
                raise SocialError("unknown_event")
            return IncomingEvent(scope, row["sender_id"], row["message_id"], row["occurred_at"], row["received_at"], row["text"],
                                 bool(row["source_verified"]), bool(row["is_self"]), bool(row["mention_self"]), row["reply_to"])

    def action_in_scope(self, scope: ConversationScope, action_id):
        with self._transaction() as db:
            return db.execute("SELECT 1 FROM outbox o JOIN inbox i USING(event_id) WHERE o.action_id=? AND i.scope=?",
                              (action_id, scope.key())).fetchone() is not None

    def claim(self, action_id) -> OutgoingAction | None:
        with self._transaction() as db:
            row = db.execute("""SELECT i.*,o.action_id,o.text AS reply,o.state AS delivery_state,o.expires_at
                                 FROM outbox o JOIN inbox i USING(event_id) WHERE o.action_id=?""", (action_id,)).fetchone()
            if row is None:
                raise SocialError("unknown_action")
            if row["delivery_state"] != "QUEUED":
                return None
            scope = ConversationScope(*json.loads(row["scope"]))
            if scope.platform == "wechat":
                binding = db.execute("SELECT * FROM bridge_bindings WHERE scope=?", (row["scope"],)).fetchone()
                if (scope.conversation_type != "dm" or scope.conversation_id != "filehelper"
                        or not binding or not binding["active"] or binding["blocked_reason"]
                        or binding["mode"] != "draft" or binding["generation"] != row["bridge_generation"]):
                    db.execute("UPDATE outbox SET state='CANCELLED',reason='wechat_send_target_denied' WHERE action_id=?", (action_id,))
                    return None
            if self.now() >= row["expires_at"]:
                db.execute("UPDATE outbox SET state='EXPIRED',reason='draft_expired' WHERE action_id=?", (action_id,))
                return None
            if not self._valid(db, row):
                db.execute("UPDATE outbox SET state='CANCELLED',reason='stale_context_or_policy' WHERE action_id=?", (action_id,))
                return None
            if db.execute("SELECT 1 FROM group_policies WHERE scope=?", (row["scope"],)).fetchone():
                from .groups import budget_reason

                reason = budget_reason(db, row["scope"], self.now(), exclude_action=action_id)
                if reason:
                    db.execute("UPDATE outbox SET state='CANCELLED',reason=? WHERE action_id=?", (reason, action_id))
                    return None
            db.execute("UPDATE outbox SET state='SENDING' WHERE action_id=?", (action_id,))
            db.execute("INSERT INTO attempts VALUES(?,?,'SENDING',NULL)", (action_id, self.now()))
            return OutgoingAction(action_id, ConversationScope(*json.loads(row["scope"])), row["message_id"], row["reply"])

    def finish_delivery(self, action_id, state, reason=None, receipt_id=None):
        if state not in {"CONFIRMED", "UNCONFIRMED", "FAILED"}:
            raise SocialError("invalid_transition")
        with self._transaction() as db:
            changed = db.execute("UPDATE outbox SET state=?,reason=?,receipt_id=? WHERE action_id=? AND state='SENDING'",
                                 (state, reason, receipt_id, action_id)).rowcount
            if changed != 1:
                raise SocialError("invalid_transition")
            db.execute("UPDATE attempts SET state=?,finished_at=? WHERE action_id=?", (state, self.now(), action_id))
            # Fake receipts historically are synthetic message IDs. Real evidence
            # is reconciled separately and never treats a manual receipt as one.
            row = db.execute("SELECT i.scope FROM inbox i JOIN outbox o USING(event_id) WHERE o.action_id=?", (action_id,)).fetchone()
            if state == "CONFIRMED" and json.loads(row[0])[0] == "fake":
                db.execute("UPDATE outbox SET platform_message_id=? WHERE action_id=?", (receipt_id, action_id))

    def counts(self):
        with self._transaction() as db:
            return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in ("inbox", "decisions", "outbox", "attempts")}
