"""Explicit single-private-target observation, isolated from filehelper sends/cursors."""

import hashlib
import json
import re

from ...context import ConversationScope
from ...social.models import SocialError, identifier
from .contracts import AccountBinding, ConversationBinding, Cursor
from .receive_process import ReaderSession
from .receive_state import ReaderCatalog, fingerprint


def migrate(db):
    db.execute("""CREATE TABLE reader_private_targets(
        fingerprint TEXT NOT NULL REFERENCES reader_accounts(fingerprint),username TEXT NOT NULL,
        evidence_ref TEXT NOT NULL,planned_baselines TEXT NOT NULL,created_at REAL NOT NULL,
        PRIMARY KEY(fingerprint,username))""")
    db.execute("PRAGMA user_version=10")


def private_scope(data):
    fields = {"version", "account_fingerprint", "username", "kind", "mode", "model_allowed", "send_allowed",
              "evidence_ref", "operator_label", "selection_reference", "identity_evidence"}
    if (type(data) is not dict or set(data) != fields or type(data["version"]) is not int or data["version"] != 1
            or data["kind"] != "dm" or data["mode"] != "observe"
            or data["model_allowed"] is not False or data["send_allowed"] is not False):
        raise SocialError("private_readonly_scope_required")
    fingerprint(data["account_fingerprint"])
    username = data["username"]
    if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", username)
            or username == "filehelper"):
        raise SocialError("private_target_identifier_denied")
    if data["evidence_ref"] != "r3-selected-native:" + hashlib.sha256(username.encode()).hexdigest():
        raise SocialError("private_target_evidence_mismatch")
    for key in ("operator_label", "selection_reference", "identity_evidence"):
        if not isinstance(data[key], str) or not 1 <= len(data[key]) <= 2048:
            raise SocialError("private_target_metadata_invalid")
    return dict(data)


def ensure_private_selection_current(ledger, selection):
    account = ledger._db.execute("SELECT account_id FROM reader_accounts WHERE fingerprint=?", (selection["account_fingerprint"],)).fetchone()
    if account:
        key = ConversationScope("wechat", account[0], "dm", selection["username"]).key()
        old = ledger._db.execute("SELECT blocked_reason FROM bridge_bindings WHERE scope=?", (key,)).fetchone()
        if old and old[0] == "private_target_replaced":
            raise SocialError("private_target_selection_retired")


def replace_private_selection(ledger, new_scope, *, previous_target_sha256, operator_reference):
    """Explicit operator replacement retains old plans/cursors and revokes old reader startup."""
    selected = private_scope(new_scope)
    fingerprint(previous_target_sha256)
    identifier(operator_reference)
    with ledger._transaction() as db:
        account = db.execute("SELECT account_id FROM reader_accounts WHERE fingerprint=?", (selected["account_fingerprint"],)).fetchone()
        if not account:
            raise SocialError("private_reader_original_account_required")
        candidates = [r for r in db.execute("SELECT username FROM reader_private_targets WHERE fingerprint=?", (selected["account_fingerprint"],))
                      if hashlib.sha256(r[0].encode()).hexdigest() == previous_target_sha256]
        if len(candidates) != 1 or candidates[0][0] == selected["username"]:
            raise SocialError("private_target_replacement_scope_mismatch")
        key = ConversationScope("wechat", account[0], "dm", candidates[0][0]).key()
        old = db.execute("SELECT active,mode,blocked_reason FROM bridge_bindings WHERE scope=?", (key,)).fetchone()
        if not old or old[0] or old[1] != "observe" or db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0").fetchone():
            raise SocialError("private_target_replacement_busy")
        if old[2] not in (None, "private_target_replaced"):
            raise SocialError("private_target_replacement_block_changed")
        db.execute("UPDATE bridge_bindings SET active=0,blocked_reason='private_target_replaced' WHERE scope=?", (key,))
        db.execute("UPDATE scopes SET enabled=0 WHERE scope=?", (key,))
    return dict(previous_target_sha256=previous_target_sha256, target_sha256=hashlib.sha256(selected["username"].encode()).hexdigest(),
                operator_reference=operator_reference, old_selection_retired=True)


class PrivateReaderCatalog(ReaderCatalog):
    def __init__(self, ledger, scope):
        super().__init__(ledger)
        self.selection = private_scope(scope)

    def attach(self, manifest, window, expected, *, initialize=False, mode="observe"):
        selection = self.selection
        ensure_private_selection_current(self.ledger, selection)
        if mode != "observe" or expected != selection["account_fingerprint"]:
            raise SocialError("private_readonly_scope_required")
        if type(manifest) is not dict or set(manifest) != {"account_id", "root_identity", "shards", "target_username"}:
            raise SocialError("invalid_private_reader_manifest")
        account, username = manifest["account_id"], selection["username"]
        identifier(account)
        if (hashlib.sha256(account.encode()).hexdigest() != expected or account == username
                or manifest["target_username"] != username):
            raise SocialError("private_reader_identity_mismatch")
        shards = manifest["shards"]
        if type(shards) is not dict or not shards:
            raise SocialError("private_reader_shards_invalid")
        with self.ledger._transaction() as db:
            old = db.execute("SELECT * FROM reader_accounts WHERE fingerprint=?", (expected,)).fetchone()
            if not old or old["account_id"] != account or old["root_identity"] != manifest["root_identity"]:
                raise SocialError("private_reader_original_account_required")
            registered = {row["name"]: row for row in db.execute("SELECT * FROM reader_shards WHERE fingerprint=?", (expected,))}
            # R3 first slice deliberately refuses physical rotation/additions; no account-wide catalog mutation.
            if set(registered) != set(shards):
                raise SocialError("private_reader_shard_set_changed")
            for name, item in shards.items():
                if type(item) is not dict or set(item) != {"identity", "tail", "local_fence"}:
                    raise SocialError("invalid_private_reader_manifest")
                if item["identity"] != registered[name]["identity"]:
                    raise SocialError("private_reader_shard_replaced")
                tail = Cursor(**item["tail"])
                if type(item["local_fence"]) is not int or not tail.local_id <= item["local_fence"] <= 2**63 - 1:
                    raise SocialError("invalid_local_fence")
            plan = db.execute("SELECT * FROM reader_private_targets WHERE fingerprint=? AND username=?", (expected, username)).fetchone()
            if not plan:
                for previous in db.execute("SELECT username FROM reader_private_targets WHERE fingerprint=?", (expected,)):
                    key = ConversationScope("wechat", account, "dm", previous[0]).key()
                    blocked = db.execute("SELECT blocked_reason FROM bridge_bindings WHERE scope=?", (key,)).fetchone()
                    if not blocked or blocked[0] != "private_target_replaced":
                        raise SocialError("single_private_target_only")
                if not initialize:
                    raise SocialError("private_reader_explicit_baseline_required")
                baselines = {name: dict(shard=registered[name]["logical_shard"], start=item["tail"], local_fence=item["local_fence"])
                             for name, item in shards.items()}
                db.execute("INSERT INTO reader_private_targets VALUES(?,?,?,?,?)",
                           (expected, username, selection["evidence_ref"], json.dumps(baselines), self.ledger.now()))
            else:
                if plan["evidence_ref"] != selection["evidence_ref"]:
                    raise SocialError("private_target_evidence_changed")
                baselines = json.loads(plan["planned_baselines"])
            epoch = old["db_epoch"]
        binding = ConversationBinding(AccountBinding(account, epoch, account, window, "w2a-authenticated-reader-v1"),
                                      username, "dm", selection["evidence_ref"])
        self.store.bind(binding, mode="observe")
        try:
            for name, plan in baselines.items():
                if plan["shard"] != registered[name]["logical_shard"]:
                    raise SocialError("private_reader_baseline_shard_changed")
                if not self.ledger._db.execute("SELECT 1 FROM bridge_cursors WHERE source_key=?", (binding.source_key(plan["shard"]),)).fetchone():
                    self.store.baseline(binding, plan["shard"], Cursor(**plan["start"]), local_fence=plan["local_fence"])
        except BaseException:
            self.store.unbind(binding)
            raise
        self.binding, self.expected = binding, expected
        return binding


class PrivateReaderSession(ReaderSession):
    read_only_private = True

    def __init__(self, ledger, worker, window, expected, scope, **kwargs):
        super().__init__(ledger, worker, window, expected, mode="observe", **kwargs)
        self.catalog = PrivateReaderCatalog(ledger, scope)

    def validate_page_records(self, records):
        for record in records:
            if (record.kind != "dm" or record.conversation != self.catalog.selection["username"]
                    or record.sender_id not in {self.binding.account.self_id, self.catalog.selection["username"]}):
                raise SocialError("private_sender_identity_mismatch")
