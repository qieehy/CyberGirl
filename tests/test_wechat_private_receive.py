"""One canonical target, persistent initial baseline, original sender/filehelper isolation."""

import asyncio
import hashlib
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_wechat_receiver import EXPECTED, MANIFEST, WINDOW
from wechat_agent.bridges.wechat.contracts import Cursor, SourceRecord
from wechat_agent.bridges.wechat.private_receive import (
    PrivateReaderCatalog,
    PrivateReaderSession,
    ensure_private_selection_current,
    private_scope,
    replace_private_selection,
)
from wechat_agent.bridges.wechat.receive_state import ReaderCatalog
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.workbench.service import WeChatWorkbenchService


def selection():
    username = "wxid_selected_peer"
    return dict(version=1, account_fingerprint=EXPECTED, username=username, kind="dm", mode="observe",
                model_allowed=False, send_allowed=False, evidence_ref="r3-selected-native:" + hashlib.sha256(username.encode()).hexdigest(),
                operator_label="fixture", selection_reference="operator-selected", identity_evidence="local-proof.json")


@pytest.mark.parametrize("capability", ["generator", "model", "sender"])
def test_private_observer_cannot_construct_model_or_send_service(capability):
    with pytest.raises(SocialError, match="private_readonly_scope_required"):
        WeChatWorkbenchService(None, SimpleNamespace(read_only_private=True), **{capability: object()})


def owner_manifest():
    value = deepcopy(MANIFEST)
    value["target_username"] = selection()["username"]
    value["shards"]["message_0.db"].update(tail=dict(sort_seq=100, local_id=20), local_fence=20)
    return value


def test_target_baseline_crash_does_not_reuse_filehelper_or_skip_on_restart(tmp_path, monkeypatch):
    path = tmp_path / "private.db"
    with Ledger(path) as ledger:
        original = ReaderCatalog(ledger)
        helper = original.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
        helper_cursors = original.checkpoints()
        original.store.unbind(helper)
        private = PrivateReaderCatalog(ledger, selection())
        with pytest.raises(SocialError, match="explicit_baseline_required"):
            private.attach(owner_manifest(), WINDOW, EXPECTED)
        monkeypatch.setattr(private.store, "baseline", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
        with pytest.raises(RuntimeError):
            private.attach(owner_manifest(), WINDOW, EXPECTED, initialize=True)
        assert ledger._db.execute("SELECT COUNT(*) FROM reader_private_targets").fetchone()[0] == 1
        assert ledger._db.execute("SELECT COUNT(*) FROM bridge_bindings WHERE active=1").fetchone()[0] == 0
    with Ledger(path, now=lambda: 1_800_000_000) as ledger:
        private = PrivateReaderCatalog(ledger, selection())
        advanced = owner_manifest()
        advanced["shards"]["message_0.db"].update(tail=dict(sort_seq=999, local_id=999), local_fence=999)
        binding = private.attach(advanced, WINDOW, EXPECTED)
        point = private.checkpoints()["message_0.db"]
        assert point["start"] == dict(sort_seq=100, local_id=20)
        assert binding.account.db_epoch == ledger._db.execute("SELECT db_epoch FROM reader_accounts").fetchone()[0]
        record = SourceRecord(binding.account.account_id, binding.account.db_epoch, binding.username, "dm", point["shard"],
            21, 101, binding.username, ledger.now(), ledger.now(), "selected inbound")
        result = private.store.ingest(binding, point["shard"], Cursor(100, 20), (record,))
        assert result["records"][0]["disposition"] == "OBSERVED"
        assert ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        private.store.unbind(binding)
        helper = ReaderCatalog(ledger)
        helper.attach(MANIFEST, WINDOW, EXPECTED)
        assert helper.checkpoints() == helper_cursors


@pytest.mark.parametrize("damage", ["send", "model", "draft", "filehelper", "group", "evidence", "extra"])
def test_private_scope_cannot_be_upgraded_or_redirected(damage):
    scope = selection()
    if damage == "send":
        scope["send_allowed"] = True
    elif damage == "model":
        scope["model_allowed"] = True
    elif damage == "draft":
        scope["mode"] = "draft"
    elif damage in {"filehelper", "group"}:
        scope["username"] = "filehelper" if damage == "filehelper" else "123@chatroom"
    elif damage == "evidence":
        scope["evidence_ref"] = "different"
    else:
        scope["extra"] = True
    with pytest.raises(SocialError):
        private_scope(scope)


@pytest.mark.parametrize("damage", ["target", "account", "root", "shard", "additional", "draft"])
def test_manifest_change_never_registers_target_or_moves_helper_cursor(tmp_path, damage):
    with Ledger(tmp_path / "private.db") as ledger:
        helper = ReaderCatalog(ledger)
        original = helper.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
        cursors = helper.checkpoints()
        helper.store.unbind(original)
        manifest = owner_manifest()
        if damage == "target":
            manifest["target_username"] = "other"
        elif damage == "account":
            manifest["account_id"] = "other"
        elif damage == "root":
            manifest["root_identity"] = "f" * 64
        elif damage == "shard":
            manifest["shards"]["message_0.db"]["identity"] = "f" * 64
        elif damage == "additional":
            manifest["shards"]["message_1.db"] = deepcopy(manifest["shards"]["message_0.db"])
        with pytest.raises(SocialError):
            PrivateReaderCatalog(ledger, selection()).attach(manifest, WINDOW, EXPECTED, initialize=True,
                mode="draft" if damage == "draft" else "observe")
        assert ledger._db.execute("SELECT COUNT(*) FROM reader_private_targets").fetchone()[0] == 0
        helper.attach(MANIFEST, WINDOW, EXPECTED)
        assert helper.checkpoints() == cursors


def test_parent_rejects_third_sender_before_commit_or_ack(tmp_path):
    with Ledger(tmp_path / "private.db") as ledger:
        helper = ReaderCatalog(ledger)
        helper.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
        private = PrivateReaderSession(ledger, None, WINDOW, EXPECTED, selection())
        private.binding = private.catalog.attach(owner_manifest(), WINDOW, EXPECTED, initialize=True)
        record = SourceRecord("fixture-account", private.binding.account.db_epoch, selection()["username"], "dm",
            "selected", 1, 1, "third_sender", ledger.now(), ledger.now(), "not selected")
        with pytest.raises(SocialError, match="sender_identity_mismatch"):
            private.validate_page_records([record])


def test_real_parent_poll_rejects_sender_without_message_commit_or_ack(tmp_path):
    async def run():
        class Worker:
            acked = stopped = False

            async def start(self, *_):
                return owner_manifest()

            async def rpc(self, command, **data):
                if command == "manifest":
                    return owner_manifest()
                if command == "page":
                    return dict(records=[dict(account_id="fixture-account", db_epoch=data["epoch"],
                        conversation=selection()["username"], kind="dm", shard=data["checkpoint"]["shard"],
                        local_id=21, sort_seq=101, sender_id="third_sender", occurred_at=1_800_000_000,
                        received_at=1_800_000_000, text="not selected")])
                self.acked = True
                return dict(accepted=True)

            async def stop(self):
                self.stopped = True
                return True

        with Ledger(tmp_path / "reject.db", now=lambda: 1_800_000_000) as ledger:
            helper = ReaderCatalog(ledger)
            old = helper.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
            helper.store.unbind(old)
            worker = Worker()
            receiver = PrivateReaderSession(ledger, worker, WINDOW, EXPECTED, selection())
            await receiver.start(initialize=True)
            with pytest.raises(SocialError, match="sender_identity_mismatch"):
                await receiver.poll()
            assert worker.stopped and not worker.acked
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 0
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_bindings WHERE active=1").fetchone()[0] == 0
    asyncio.run(run())


def test_schema9_backup_and_failed_private_migration_rollback(tmp_path, monkeypatch):
    from wechat_agent.bridges.wechat import private_receive

    path = tmp_path / "migration.db"
    with Ledger(path) as ledger:
        ledger._db.execute("DROP TABLE reader_private_targets")
        ledger._db.execute("PRAGMA user_version=9")
    original = private_receive.migrate

    def fail(db):
        original(db)
        raise OSError("migration stopped")

    monkeypatch.setattr(private_receive, "migrate", fail)
    with pytest.raises(OSError), Ledger(path):
        pass
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='reader_private_targets'").fetchone()
    assert list(tmp_path.glob("*.v9-*.bak"))


def test_explicit_replacement_preserves_old_plan_and_cursor_and_fences_old_scope(tmp_path):
    with Ledger(tmp_path / "replace.db") as ledger:
        helper = ReaderCatalog(ledger)
        h = helper.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
        helper.store.unbind(h)
        old = PrivateReaderCatalog(ledger, selection())
        binding = old.attach(owner_manifest(), WINDOW, EXPECTED, initialize=True)
        saved = old.checkpoints()
        new = selection()
        new["username"] = "wxid_replacement_peer"
        new["evidence_ref"] = "r3-selected-native:" + hashlib.sha256(new["username"].encode()).hexdigest()
        old_hash = hashlib.sha256(selection()["username"].encode()).hexdigest()
        with pytest.raises(SocialError, match="replacement_busy"):
            replace_private_selection(ledger, new, previous_target_sha256=old_hash, operator_reference="explicit-new-selection")
        old.store.unbind(binding)
        with pytest.raises(SocialError, match="scope_mismatch"):
            replace_private_selection(ledger, new, previous_target_sha256="0" * 64, operator_reference="explicit-new-selection")
        replace_private_selection(ledger, new, previous_target_sha256=old_hash, operator_reference="explicit-new-selection")
        with pytest.raises(SocialError, match="selection_retired"):
            ensure_private_selection_current(ledger, selection())
        with pytest.raises(SocialError, match="selection_retired"):
            old.attach(owner_manifest(), WINDOW, EXPECTED)
        manifest = owner_manifest()
        manifest["target_username"] = new["username"]
        PrivateReaderCatalog(ledger, new).attach(manifest, WINDOW, EXPECTED, initialize=True)
        assert ledger._db.execute("SELECT COUNT(*) FROM reader_private_targets").fetchone()[0] == 2
        assert ledger._db.execute("SELECT local_id FROM bridge_cursors WHERE source_key=?", (binding.source_key(saved["message_0.db"]["shard"]),)).fetchone()[0] == 20
