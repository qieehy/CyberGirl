"""Critical bridge scenarios only: no WeChat, credentials, UI or network access."""

import hashlib
import sqlite3
import subprocess
import sys
from dataclasses import replace

import pytest

from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    DeliveryEvidence,
    SourceRecord,
    WindowBinding,
    require_send_target,
)
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, OutgoingAction, SocialError

NOW = 1_800_000_000.0
WINDOW = WindowBinding(123, 123456789, 456, "a" * 64, "b" * 64)
ACCOUNT = AccountBinding("fixture-account", "fixture-db-epoch", "fixture-self", WINDOW, "synthetic-evidence")
BINDING = ConversationBinding(ACCOUNT, "filehelper", "dm", "synthetic-target-evidence")
ZERO = Cursor(0, 0)


def record(local_id=1, *, binding=BINDING, **kwargs):
    values = dict(account_id=binding.account.account_id, db_epoch=binding.account.db_epoch,
                  conversation=binding.username, kind=binding.kind, shard="message_0",
                  local_id=local_id, sort_seq=10, sender_id="fixture-sender", occurred_at=NOW,
                  received_at=NOW, text="same body @fixture-self")
    values.update(kwargs)
    return SourceRecord(**values)


def bind(ledger, binding=BINDING, *, mode="observe"):
    store = BridgeStore(ledger)
    store.bind(binding, mode=mode)
    store.baseline(binding, "message_0", ZERO)
    return store


def queue(ledger, store, local_id=1, *, binding=BINDING):
    ledger.configure_owner(binding.scope, "fixture-sender")
    event = record(local_id, binding=binding)
    store.ingest(binding, "message_0", store.cursor(binding, "message_0"), (event,))
    key = event.event(binding).key
    assert ledger.start_decision(key)
    ledger.finish_decision(key, Decision(ActionKind.REPLY, "synthetic reply"))
    return ledger.summary(key)["action_id"]


def test_identity_fences_send_target_before_any_input_and_restart_keeps_message_identity(tmp_path):
    event = record()
    restarted = replace(BINDING, account=replace(ACCOUNT, window=replace(WINDOW, pid=999, created_filetime=999)))
    assert BINDING.generation != restarted.generation
    assert event.event(BINDING).key == event.event(restarted).key
    assert record(db_epoch="new-database").message_id != event.message_id
    assert record(shard="message_1").message_id != event.message_id
    assert not event.event(BINDING).mention_self  # Text regex never manufactures @ evidence.
    with pytest.raises(SocialError, match="unverified_message_structure"):
        record(mentioned_ids=(ACCOUNT.self_id,))
    structured = record(mentioned_ids=(ACCOUNT.self_id,), structure_evidence_ref="fixture-structure")
    assert structured.event(BINDING).mention_self
    with pytest.raises(SocialError, match="source_binding_mismatch"):
        replace(event, account_id="different-account").event(BINDING)
    action = OutgoingAction("fixture-action", BINDING.scope, "fixture-source", "test")
    touched = []
    for target, observed in [(replace(BINDING, username="文件传输助手"), "文件传输助手"),
                             (replace(BINDING, username="contact"), "contact"),
                             (replace(BINDING, kind="group"), "filehelper"),
                             (BINDING, "some-contact")]:
        with pytest.raises(SocialError, match="wechat_send_target_denied"):
            require_send_target(target, replace(action, scope=target.scope), observed_account=ACCOUNT.account_id,
                                observed_username=observed, observed_window=WINDOW)
            touched.append("input")
    with pytest.raises(SocialError, match="wechat_send_target_denied"):
        require_send_target(BINDING, action, observed_account=ACCOUNT.account_id,
                            observed_username="filehelper", observed_window=restarted.account.window)
    assert touched == []
    require_send_target(BINDING, action, observed_account=ACCOUNT.account_id,
                        observed_username="filehelper", observed_window=WINDOW)
    with Ledger(tmp_path / "targets.db", now=lambda: NOW) as ledger:
        other = replace(BINDING, username="contact")
        store = bind(ledger, other, mode="draft")
        denied = queue(ledger, store, binding=other)
        assert ledger.claim(denied) is None
        assert ledger.action_summary(denied)["reason"] == "wechat_send_target_denied"
        assert ledger.counts()["attempts"] == 0
        store = bind(ledger, mode="draft")
        old = queue(ledger, store)
        store.bind(restarted, mode="draft")
        assert ledger.claim(old) is None
        assert ledger.action_summary(old)["state"] == "CANCELLED"
        with pytest.raises(SocialError, match="inactive_bridge_binding"):
            store.ingest(BINDING, "message_0", ZERO, (event,))


def test_atomic_receive_boundary_replay_observation_filters_and_conflict_stop(tmp_path):
    with Ledger(tmp_path / "receive.db", now=lambda: NOW) as ledger:
        store = bind(ledger)
        first, second = record(1), record(2)  # Equal sequence/body, distinct records.
        ledger._db.execute("""CREATE TRIGGER fail_cursor BEFORE UPDATE ON bridge_cursors
                              BEGIN SELECT RAISE(ABORT,'fixture failure before commit'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            store.ingest(BINDING, "message_0", ZERO, (first, second))
        assert ledger.counts()["inbox"] == 0
        assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 0
        assert store.cursor(BINDING, "message_0") == ZERO
        ledger._db.execute("DROP TRIGGER fail_cursor")
        committed = store.ingest(BINDING, "message_0", ZERO, (first,))
        assert committed["cursor"] == Cursor(10, 1)
        replay = store.ingest(BINDING, "message_0", ZERO, (replace(first, received_at=NOW + 1),))
        assert not replay["records"][0]["fresh"]
        store.ingest(BINDING, "message_0", Cursor(10, 1), (second,))
        assert ledger.counts() == {"inbox": 2, "decisions": 0, "outbox": 0, "attempts": 0}
        for item in (first, second):
            assert ledger.summary(item.event(BINDING).key)["state"] == "OBSERVED"
            assert not ledger.start_decision(item.event(BINDING).key)
        skipped = (record(3, sender_id=ACCOUNT.self_id), record(4, occurred_at=NOW - 121),
                   record(5, message_type="image", text=""))
        result = store.ingest(BINDING, "message_0", Cursor(10, 2), skipped)
        assert [r["disposition"] for r in result["records"]] == ["self_echo", "stale_event", "unsupported_message_type"]
        ledger.pause(True)
        with pytest.raises(SocialError, match="bridge_paused"):
            store.ingest(BINDING, "message_0", Cursor(10, 5), (record(6),))
        assert store.cursor(BINDING, "message_0") == Cursor(10, 5)
        ledger.pause(False)
        ledger._db.execute("""CREATE TRIGGER full_inbox BEFORE INSERT ON inbox
                              BEGIN SELECT RAISE(ABORT,'fixture capacity'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            store.ingest(BINDING, "message_0", Cursor(10, 5), (record(6),))
        assert store.cursor(BINDING, "message_0") == Cursor(10, 5)
        ledger._db.execute("DROP TRIGGER full_inbox")
        ledger._db.execute("""WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<9995)
            INSERT INTO bridge_records SELECT 'capacity-fixture-'||x,?,'fixture',0,x,'fixture',NULL FROM n""",
                           (BINDING.source_key("message_0"),))
        with pytest.raises(SocialError, match="bridge_capacity"):
            store.ingest(BINDING, "message_0", Cursor(10, 5), (record(6),))
        assert store.cursor(BINDING, "message_0") == Cursor(10, 5)
        ledger._db.execute("DELETE FROM bridge_records WHERE disposition='fixture'")
        with pytest.raises(SocialError, match="cursor_mismatch"):
            store.ingest(BINDING, "message_0", ZERO, (record(6),))
        with pytest.raises(SocialError, match="event_identity_conflict"):
            store.ingest(BINDING, "message_0", ZERO, (replace(first, text="conflicting text"),))
        assert ledger._db.execute("SELECT local_id FROM bridge_cursors").fetchone()[0] == 5
        assert ledger.counts()["inbox"] == 2
        with pytest.raises(SocialError, match="source_requires_review"):
            store.bind(BINDING)


def test_receive_process_exit_after_commit_before_ack_preserves_cursor_and_never_decides(tmp_path):
    path = tmp_path / "crash.db"
    code = '''import os,sys
from pathlib import Path
from tests.test_wechat_bridge import NOW,BINDING,ZERO,bind,record
from wechat_agent.social.ledger import Ledger
with Ledger(Path(sys.argv[1]),now=lambda:NOW) as ledger:
    store=bind(ledger)
    store.ingest(BINDING,'message_0',ZERO,(record(),))
    os._exit(37)
'''
    child = subprocess.run([sys.executable, "-B", "-c", code, str(path)], capture_output=True, timeout=20)
    assert child.returncode == 37, child.stderr.decode(errors="replace")
    with Ledger(path, now=lambda: NOW) as ledger:
        store = BridgeStore(ledger)
        with pytest.raises(SocialError, match="inactive_bridge_binding"):
            store.cursor(BINDING, "message_0")
        store.bind(BINDING)
        assert store.baseline(BINDING, "message_0", Cursor(999, 999)) == Cursor(10, 1)
        result = store.ingest(BINDING, "message_0", ZERO, (record(),))
        assert result["records"][0]["fresh"] is False
        assert ledger.counts() == {"inbox": 1, "decisions": 0, "outbox": 0, "attempts": 0}


def test_unknown_reconciliation_preserves_attempt_and_separates_manual_receipt_from_message(tmp_path):
    path = tmp_path / "evidence.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        store = bind(ledger, mode="draft")
        action_id = queue(ledger, store)
        action = ledger.claim(action_id)
        assert action is not None
        ledger.finish_delivery(action_id, "UNCONFIRMED", "fixture_uncertainty")
        echo = DeliveryEvidence("echo-1", action_id, BINDING.scope, BINDING.generation, "local_echo", "source-row",
                                hashlib.sha256(action.text.encode()).hexdigest(), "native-id-1")
        with pytest.raises(SocialError, match="evidence_action_mismatch"):
            store.record_evidence(replace(echo, content_sha256="0" * 64))
        assert store.record_evidence(echo)
        assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
        assert ledger.claim(action_id) is None
        confirmation = replace(echo, evidence_id="manual-1", kind="manual_confirmation", reference="operator-confirmation",
                               platform_message_id=None)
        assert store.record_evidence(confirmation)
        assert not store.record_evidence(confirmation)
        row = ledger._db.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,)).fetchone()
        assert (row["state"], row["receipt_id"], row["platform_message_id"]) == ("CONFIRMED", "operator-confirmation", "native-id-1")
        assert ledger._db.execute("SELECT state FROM attempts").fetchone()[0] == "UNCONFIRMED"
        second_id = queue(ledger, store, 2)
        second = ledger.claim(second_id)
        ledger.finish_delivery(second_id, "UNCONFIRMED")
        manual_only = replace(confirmation, evidence_id="manual-2", action_id=second_id,
                              content_sha256=hashlib.sha256(second.text.encode()).hexdigest())
        store.record_evidence(manual_only)
        assert ledger._db.execute("SELECT platform_message_id FROM outbox WHERE action_id=?", (second_id,)).fetchone()[0] is None
        assert ledger.counts()["attempts"] == 2
    with Ledger(path, now=lambda: NOW) as reopened:
        assert reopened.claim(action_id) is None
        assert reopened.claim(second_id) is None
        assert reopened.counts()["attempts"] == 2


def test_v4_migration_backup_and_rollback_keep_existing_facts(tmp_path, monkeypatch):
    from wechat_agent.bridges.wechat import store as store_module
    from wechat_agent.context import ConversationScope
    from wechat_agent.social.models import IncomingEvent

    path = tmp_path / "migration.db"
    # Make a populated prior-version fixture from the existing schema components.
    with Ledger(path, now=lambda: NOW) as ledger:
        scope = ConversationScope("fake", "fixture", "dm", "owner")
        ledger.configure_owner(scope, "owner")
        event = IncomingEvent(scope, "owner", "legacy", NOW, NOW, "fixture", source_verified=True)
        ledger.accept(event)
        ledger.start_decision(event.key)
        ledger.finish_decision(event.key, Decision(ActionKind.REPLY, "legacy reply"))
        action_id = ledger.summary(event.key)["action_id"]
        ledger.claim(action_id)
        ledger.finish_delivery(action_id, "CONFIRMED", receipt_id="legacy-native-id")
        ledger.save_persona(1, "fixture", "style", "notes")
        db = ledger._db
        for name in ("reader_private_targets", "workbench_requests", "workbench_draft_revisions", "workbench_drafts", "workbench_context", "bridge_message_payloads",
                     "wechat_channel_reviews", "reader_shards", "reader_accounts", "wechat_send_journal", "wechat_send_operations", "wechat_send_control",
                     "delivery_evidence", "bridge_records", "bridge_cursors", "bridge_bindings"):
            db.execute(f"DROP TABLE {name}")
        db.execute("ALTER TABLE inbox DROP COLUMN bridge_generation")
        db.execute("ALTER TABLE outbox DROP COLUMN platform_message_id")
        db.execute("ALTER TABLE inbox DROP COLUMN origin")
        db.execute("PRAGMA user_version=4")
    original = store_module.migrate

    def fail_migration(db):
        original(db)
        raise RuntimeError("fixture migration interrupted")

    monkeypatch.setattr(store_module, "migrate", fail_migration)
    with pytest.raises(RuntimeError, match="fixture migration interrupted"), Ledger(path, now=lambda: NOW):
        pass
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not check.execute("SELECT 1 FROM sqlite_master WHERE name='bridge_bindings'").fetchone()
        assert "bridge_generation" not in [r[1] for r in check.execute("PRAGMA table_info(inbox)")]
    monkeypatch.setattr(store_module, "migrate", original)
    with Ledger(path, now=lambda: NOW) as upgraded:
        assert upgraded._db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert upgraded.current_persona().name == "fixture"
        row = upgraded._db.execute("SELECT state,receipt_id,platform_message_id FROM outbox").fetchone()
        assert tuple(row) == ("CONFIRMED", "legacy-native-id", "legacy-native-id")
    backups = list(tmp_path.glob("*.v4-*.bak"))
    assert backups
    for backup in backups:
        with sqlite3.connect(backup) as check:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 4
            assert check.execute("SELECT name FROM personas WHERE version=2").fetchone()[0] == "fixture"
            assert check.execute("SELECT receipt_id FROM outbox").fetchone()[0] == "legacy-native-id"
