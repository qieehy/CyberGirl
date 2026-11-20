"""Only discarded snapshot reads can retry; cursors and ACK remain transactional."""
import asyncio

import pytest

from scripts.wechat_reader_worker import SnapshotReadAttempts
from tests.test_wechat_receiver import EXPECTED, WINDOW, worker
from wechat_agent.bridges.wechat.receive_process import ReaderSession
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("code", ["source_changed_during_snapshot", "reader_account_changed", "reader_shard_replaced", "snapshot_format_or_bound"])
def test_only_concurrent_snapshot_change_is_retried_with_fixed_bound(code):
    calls, sleeps = [], []
    def fail():
        calls.append(1)
        raise SocialError(code)
    attempts = SnapshotReadAttempts(sleep=sleeps.append)
    with pytest.raises(SocialError, match=code):
        attempts.read(fail)
    assert len(calls) == (3 if code == "source_changed_during_snapshot" else 1)
    assert sleeps == ([0.03, 0.03] if len(calls) == 3 else [])
    assert attempts.metrics() == dict(snapshot_retries=len(sleeps), snapshot_retry_exhausted=int(len(calls) == 3))


def test_real_reader_pipe_recovers_snapshots_without_duplicate_ingest_or_ack(tmp_path):
    async def run():
        with Ledger(tmp_path / "original.db", now=lambda: 1_800_000_000) as ledger:
            session = ReaderSession(ledger, worker("snapshot-once"), WINDOW, EXPECTED)
            await session.start(initialize=True)
            assert await session.poll() == dict(fresh=70, replay=0, pages=3, more=False)
            assert await session.worker.rpc("read_metrics") == dict(snapshot_retries=3, snapshot_retry_exhausted=0)
            assert (await session.poll())["fresh"] == 0
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 70
            assert ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
            assert await session.close()
    asyncio.run(run())


def test_exhausted_read_retry_stops_worker_without_advancing_checkpoint(tmp_path):
    async def run():
        with Ledger(tmp_path / "original.db", now=lambda: 1_800_000_000) as ledger:
            session = ReaderSession(ledger, worker("snapshot-page-always"), WINDOW, EXPECTED)
            await session.start(initialize=True)
            with pytest.raises(SocialError, match="source_changed_during_snapshot"):
                await session.poll()
            assert session.worker.process.returncode is not None
            assert ledger._db.execute("SELECT local_id,local_fence FROM bridge_cursors").fetchone()[:] == (0, 0)
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 0
            resumed = ReaderSession(ledger, worker(), WINDOW, EXPECTED)
            await resumed.start()
            assert (await resumed.poll())["fresh"] == 70
            await resumed.close()
    asyncio.run(run())
