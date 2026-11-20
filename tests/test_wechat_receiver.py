"""Receive continuity, bounded codecs and real child failure boundaries."""

import asyncio
import hashlib
import sqlite3
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import zstandard as zstd

from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    WindowBinding,
)
from wechat_agent.bridges.wechat.reader import forward_guard, read_new
from wechat_agent.bridges.wechat.receive_process import (
    JsonReaderWorker,
    ReaderSession,
    decode_frame,
)
from wechat_agent.bridges.wechat.receive_state import ReaderCatalog
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.bridges.wechat.textcodec import decode_text
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError

WINDOW = WindowBinding(123, 123456, 456, "c" * 64, "d" * 64)
EXPECTED = hashlib.sha256(b"fixture-account").hexdigest()
MANIFEST = {"account_id": "fixture-account", "root_identity": "a" * 64, "shards": {
    "message_0.db": {"identity": "b" * 64, "tail": {"sort_seq": 5, "local_id": 3}, "local_fence": 8}}}


def test_stable_epoch_restart_rotation_replacement_and_baseline_crash(tmp_path, monkeypatch):
    path = tmp_path / "receive.db"
    with Ledger(path) as ledger:
        catalog = ReaderCatalog(ledger)
        with pytest.raises(SocialError, match="explicit_baseline_required"):
            catalog.attach(MANIFEST, WINDOW, EXPECTED)
        # Catalog commit followed by crash before baseline must retain the
        # explicitly selected tail, not restart from zero / a newer tail.
        original = catalog.store.baseline
        monkeypatch.setattr(catalog.store, "baseline", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
        with pytest.raises(RuntimeError):
            catalog.attach(MANIFEST, WINDOW, EXPECTED, initialize=True)
        assert ledger._db.execute("SELECT COUNT(*) FROM bridge_bindings WHERE active=1").fetchone()[0] == 0
        epoch = ledger._db.execute("SELECT db_epoch FROM reader_accounts").fetchone()[0]
        monkeypatch.setattr(catalog.store, "baseline", original)
    with Ledger(path) as ledger:
        catalog = ReaderCatalog(ledger)
        current = deepcopy(MANIFEST)
        current["shards"]["message_0.db"]["tail"] = {"sort_seq": 100, "local_id": 100}
        current["shards"]["message_0.db"]["local_fence"] = 100
        binding = catalog.attach(current, replace(WINDOW, pid=999), EXPECTED)
        assert binding.account.db_epoch == epoch
        checkpoints = catalog.checkpoints()
        assert checkpoints["message_0.db"]["start"] == {"sort_seq": 5, "local_id": 3}
        assert checkpoints["message_0.db"]["local_fence"] == 8
        current["shards"]["message_1.db"] = {"identity": "e" * 64,
            "tail": {"sort_seq": 500, "local_id": 2}, "local_fence": 2}
        assert catalog.attach(current, WINDOW, EXPECTED).account.db_epoch == epoch
        assert catalog.checkpoints()["message_1.db"]["start"] == {"sort_seq": 0, "local_id": 0}
        assert catalog.checkpoints()["message_0.db"] == checkpoints["message_0.db"]
        replaced = deepcopy(current)
        replaced["shards"]["message_0.db"]["identity"] = "f" * 64
        with pytest.raises(SocialError, match="shard_replaced"):
            catalog.attach(replaced, WINDOW, EXPECTED)
        with pytest.raises(SocialError, match="shard_disappeared"):
            catalog.attach(MANIFEST, WINDOW, EXPECTED)
        with pytest.raises(SocialError, match="database_replaced"):
            catalog.attach({**current, "root_identity": "f" * 64}, WINDOW, EXPECTED)


def test_codec_explicit_metadata_size_truncation_dictionary_and_trailing_data():
    text = "中文和 emoji 🌏" * 100
    compressed = zstd.ZstdCompressor().compress(text.encode())
    assert decode_text(compressed, 4) == text
    assert decode_text(text.encode(), 0) == text
    assert decode_text(zstd.ZstdCompressor(write_content_size=False).compress(text.encode()), 4) == text
    bad = [(compressed, 2), (compressed, 3), (compressed, 5), (compressed, 8),
           (compressed[:-1], 4), (compressed + b"trailing", 4), (compressed + compressed, 4),
           (b"\xff", 0), (zstd.ZstdCompressor().compress(b"x" * 16001), 4),
           (zstd.ZstdCompressor(write_content_size=False).compress(b"x" * 16001), 4)]
    for content, tag in bad:
        with pytest.raises(SocialError):
            decode_text(content, tag)


@pytest.mark.parametrize("raw", [b'{}', b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'[]\n', b'x' * 1048577 + b'\n'],
                         ids=["unterminated", "duplicate", "nonfinite", "array", "oversize"])
def test_reader_frames_fail_closed(raw):
    with pytest.raises(SocialError):
        decode_frame(raw)


def test_compressed_tied_pages_and_regressed_sequence_are_never_skipped():
    table = "Msg_" + hashlib.md5(b"filehelper", usedforsecurity=False).hexdigest()
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE Name2Id(user_name TEXT)")
        db.execute("INSERT INTO Name2Id VALUES('fixture-sender')")
        db.execute(f'''CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_type INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB,
            sort_seq INTEGER,WCDB_CT_message_content INTEGER)''')
        compressed = zstd.ZstdCompressor().compress("唯一中文标记".encode())
        db.executemany(f'INSERT INTO "{table}" VALUES(?,1,1,100,?,10,4)',
                       ((i, compressed) for i in range(1, 66)))
        binding = ConversationBinding(AccountBinding("fixture-account", "epoch", "self", WINDOW, "fixture"),
                                      "filehelper", "dm", "fixture")
        start = Cursor(0, 0)
        seen = []
        for count in (32, 32, 1):
            forward_guard(db, "filehelper", start, max(seen, default=0))
            page = read_new(db, binding, "message_0.db", start, 100, limit=32)
            assert len(page) == count and all(r.text == "唯一中文标记" for r in page)
            seen.extend(r.local_id for r in page)
            start = page[-1].position
        assert seen == list(range(1, 66))
        db.execute(f'INSERT INTO "{table}" VALUES(66,1,1,100,?,9,4)', (compressed,))
        with pytest.raises(SocialError, match="sort_sequence_regressed"):
            forward_guard(db, "filehelper", start, 65)
        db.execute(f'DELETE FROM "{table}" WHERE local_id>=65')
        with pytest.raises(SocialError, match="source_truncated"):
            forward_guard(db, "filehelper", start, 65)


def worker(mode="normal", **kwargs):
    return JsonReaderWorker(python_worker_command(Path(__file__).with_name("wechat_reader_child.py"), mode), **kwargs)


def test_real_child_composite_paging_atomic_commit_ack_and_restart(tmp_path):
    async def scenario():
        path = tmp_path / "receive.db"
        with Ledger(path, now=lambda: 1_800_000_000) as ledger:
            child = worker("ack-failure")
            session = ReaderSession(ledger, child, WINDOW, EXPECTED)
            await session.start(initialize=True)
            ledger.configure_owner(session.binding.scope, "fixture-sender")
            epoch = session.binding.account.db_epoch
            with pytest.raises(SocialError, match="reader_transport_failed"):
                await session.poll()
            assert child.process.returncode is not None
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 32
            assert ledger._db.execute("SELECT local_id,local_fence FROM bridge_cursors").fetchone()[:] == (32, 32)
        with Ledger(path, now=lambda: 1_800_000_000) as ledger:
            child = worker()
            session = ReaderSession(ledger, child, replace(WINDOW, pid=999), EXPECTED)
            checkpoints = await session.start()
            assert session.binding.account.db_epoch == epoch
            assert checkpoints["message_0.db"]["start"] == {"sort_seq": 10, "local_id": 32}
            assert await session.poll() == {"fresh": 38, "replay": 0, "pages": 2, "more": False}
            assert (await session.poll())["fresh"] == 0
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 70
            assert ledger._db.execute("SELECT COUNT(*) FROM inbox WHERE state='OBSERVED'").fetchone()[0] == 70
            assert ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
            assert await session.close()
            assert child.process.returncode is not None
    asyncio.run(scenario())


def test_failure_before_commit_and_timeout_leave_cursor_and_stop_child(tmp_path):
    async def scenario():
        with Ledger(tmp_path / "receive.db", now=lambda: 1_800_000_000) as ledger:
            session = ReaderSession(ledger, worker(), WINDOW, EXPECTED)
            await session.start(initialize=True)
            ledger._db.execute("""CREATE TRIGGER fail_receive BEFORE UPDATE ON bridge_cursors
                BEGIN SELECT RAISE(ABORT,'synthetic transaction failure'); END""")
            with pytest.raises(sqlite3.IntegrityError):
                await session.poll()
            assert session.worker.process.returncode is not None
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 0
            assert ledger._db.execute("SELECT local_id,local_fence FROM bridge_cursors").fetchone()[:] == (0, 0)
            ledger._db.execute("DROP TRIGGER fail_receive")
            slow = ReaderSession(ledger, worker("late"), WINDOW, EXPECTED)
            await slow.start()
            slow.worker.timeout = 0.25
            with pytest.raises(SocialError, match="reader_transport_failed"):
                await slow.poll()
            assert slow.worker.process.returncode is not None
            assert ledger._db.execute("SELECT local_id FROM bridge_cursors").fetchone()[0] == 0
    asyncio.run(scenario())


def test_v6_migration_backup_and_atomic_failure_preserve_cursor(tmp_path, monkeypatch):
    from wechat_agent.bridges.wechat import receive_state

    path = tmp_path / "old.db"
    with Ledger(path) as ledger:
        for name in ("reader_private_targets", "workbench_requests", "workbench_draft_revisions", "workbench_drafts", "workbench_context", "bridge_message_payloads", "wechat_channel_reviews", "reader_shards", "reader_accounts"):
            ledger._db.execute(f"DROP TABLE {name}")
        ledger._db.execute("ALTER TABLE bridge_cursors DROP COLUMN local_fence")
        ledger._db.execute("PRAGMA user_version=6")
    original = receive_state.migrate

    def fail(db):
        original(db)
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(receive_state, "migrate", fail)
    with pytest.raises(RuntimeError), Ledger(path):
        pass
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='reader_accounts'").fetchone()
    monkeypatch.setattr(receive_state, "migrate", original)
    with Ledger(path) as ledger:
        assert ledger._db.execute("PRAGMA user_version").fetchone()[0] == 10
    assert list(tmp_path.glob("old.db.v6-*.bak"))
