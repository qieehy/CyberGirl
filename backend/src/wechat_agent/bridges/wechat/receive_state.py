"""Stable account epoch and bounded shard catalog in the parent-owned ledger."""

import hashlib
import re
import secrets

from ...social.models import SocialError, identifier
from .contracts import AccountBinding, ConversationBinding, Cursor
from .store import BridgeStore

MAX_SHARDS = 16


def migrate(db):
    db.execute("""CREATE TABLE reader_accounts(
        fingerprint TEXT PRIMARY KEY,account_id TEXT UNIQUE NOT NULL,
        root_identity TEXT NOT NULL,db_epoch TEXT NOT NULL)""")
    db.execute("""CREATE TABLE reader_shards(
        fingerprint TEXT NOT NULL REFERENCES reader_accounts(fingerprint),
        name TEXT NOT NULL,identity TEXT NOT NULL,logical_shard TEXT NOT NULL,
        baseline_sort_seq INTEGER NOT NULL,baseline_local_id INTEGER NOT NULL,baseline_fence INTEGER NOT NULL,
        PRIMARY KEY(fingerprint,name))""")
    db.execute("ALTER TABLE bridge_cursors ADD COLUMN local_fence INTEGER NOT NULL DEFAULT 0")
    db.execute("UPDATE bridge_cursors SET local_fence=local_id")
    db.execute("PRAGMA user_version=7")


def fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SocialError("invalid_reader_fingerprint")


class ReaderCatalog:
    def __init__(self, ledger):
        self.ledger = ledger
        self.store = BridgeStore(ledger)

    def attach(self, manifest, window, expected, *, initialize=False, mode="observe"):
        """Authenticate first; register identity, never derive epoch from shard set."""
        fingerprint(expected)
        if set(manifest) != {"account_id", "root_identity", "shards"}:
            raise SocialError("invalid_reader_manifest")
        account = manifest["account_id"]
        identifier(account)
        if hashlib.sha256(account.encode()).hexdigest() != expected:
            raise SocialError("reader_account_mismatch")
        fingerprint(manifest["root_identity"])
        shards = manifest["shards"]
        if not isinstance(shards, dict) or not 1 <= len(shards) <= MAX_SHARDS:
            raise SocialError("reader_shard_bound")
        for name, item in shards.items():
            if not re.fullmatch(r"message_\d{1,3}\.db", name):
                raise SocialError("invalid_message_shard")
            if set(item) != {"identity", "tail", "local_fence"}:
                raise SocialError("invalid_reader_manifest")
            fingerprint(item["identity"])
            tail = Cursor(**item["tail"])
            if type(item["local_fence"]) is not int or not tail.local_id <= item["local_fence"] <= 2**63 - 1:
                raise SocialError("invalid_local_fence")
        with self.ledger._transaction() as db:
            old = db.execute("SELECT * FROM reader_accounts WHERE fingerprint=?", (expected,)).fetchone()
            if not old:
                if not initialize:
                    raise SocialError("reader_explicit_baseline_required")
                if db.execute("SELECT COUNT(*) FROM reader_accounts").fetchone()[0]:
                    raise SocialError("single_wechat_account_only")
                epoch = secrets.token_hex(32)
                db.execute("INSERT INTO reader_accounts VALUES(?,?,?,?)",
                           (expected, account, manifest["root_identity"], epoch))
            else:
                if (old["account_id"], old["root_identity"]) != (account, manifest["root_identity"]):
                    raise SocialError("reader_account_database_replaced")
                epoch = old["db_epoch"]
            registered = {r["name"]: r for r in db.execute(
                "SELECT * FROM reader_shards WHERE fingerprint=?", (expected,))}
            if not set(registered) <= set(shards):
                raise SocialError("reader_shard_disappeared")
            # Check every replacement before registering any addition.
            if any(registered[n]["identity"] != shards[n]["identity"] for n in registered):
                raise SocialError("reader_shard_replaced")
            for name, item in shards.items():
                if name not in registered:
                    logical = name + ":" + item["identity"]
                    start = Cursor(**item["tail"]) if old is None else Cursor(0, 0)
                    fence = item["local_fence"] if old is None else 0
                    db.execute("INSERT INTO reader_shards VALUES(?,?,?,?,?,?,?)",
                               (expected, name, item["identity"], logical, start.sort_seq, start.local_id, fence))
        binding = ConversationBinding(AccountBinding(account, epoch, account, window,
            "w2a-authenticated-reader-v1"), "filehelper", "dm", "w2a-explicit-filehelper-v1")
        self.store.bind(binding, mode=mode)
        try:
            self._ensure_baselines(binding, shards, expected)
        except BaseException:
            self.store.unbind(binding)
            raise
        self.binding, self.expected = binding, expected
        return binding

    def _ensure_baselines(self, binding, shards, expected):
        # Registration may commit before baseline. A restart must still know
        # whether this was an initial shard; persist that decision with catalog.
        for name, item in shards.items():
            logical = name + ":" + item["identity"]
            existing = self.ledger._db.execute("SELECT 1 FROM bridge_cursors WHERE source_key=?",
                                               (binding.source_key(logical),)).fetchone()
            if not existing:
                planned = self.ledger._db.execute(
                    "SELECT * FROM reader_shards WHERE fingerprint=? AND name=?", (expected, name)).fetchone()
                position = Cursor(planned["baseline_sort_seq"], planned["baseline_local_id"])
                fence = planned["baseline_fence"]
                self.store.baseline(binding, logical, position, local_fence=fence)

    def checkpoints(self):
        result = {}
        self.store._binding(self.ledger._db, self.binding)
        for row in self.ledger._db.execute("SELECT * FROM reader_shards WHERE fingerprint=?", (self.expected,)):
            cursor = self.ledger._db.execute("SELECT * FROM bridge_cursors WHERE source_key=?",
                (self.binding.source_key(row["logical_shard"]),)).fetchone()
            if cursor is None:
                raise SocialError("baseline_required")
            result[row["name"]] = {"shard": row["logical_shard"],
                "start": {"sort_seq": cursor["sort_seq"], "local_id": cursor["local_id"]},
                "local_fence": cursor["local_fence"]}
        return result
