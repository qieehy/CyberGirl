import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import probe_wechat_background_trial as host
from tests.test_wechat_background_journal import journal
from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat.background_process import BackgroundWorker
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("mode", ["normal", "pause", "hang"])
def test_real_child_protocol_commits_once_and_does_not_replay_after_failure(tmp_path, mode):
    path = tmp_path / "ledger.db"
    with Ledger(path) as ledger:
        recorder, _, operation, action = journal(ledger)
        chars = [0]

        def record(stage):
            if stage == "CHAR_INTENT":
                chars[0] += 1
                if mode == "pause" and chars[0] == 2:
                    ledger.pause(True)
            return recorder.record(stage)

        worker = BackgroundWorker(python_worker_command(
            Path(__file__).with_name("wechat_background_protocol_worker.py"), mode), record=record)

        async def run():
            try:
                async with asyncio.timeout(10):
                    await worker.start(operation, BINDING, action)
                    await worker._rpc("prepare")
                    if mode == "hang":
                        with pytest.raises(TimeoutError):
                            async with asyncio.timeout(0.2):
                                await worker._rpc("run_background")
                    else:
                        result = await worker._rpc("run_background")
                        assert result["control_result"]["state"] == "UNKNOWN"
                    with pytest.raises(SocialError, match="missing_or_used"):
                        await worker._rpc("run_background")
            finally:
                assert await worker.stop()

        asyncio.run(run())
        row = ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations").fetchone()
        assert tuple(row) == (1, 1 if mode == "normal" else 0)
    with Ledger(path) as ledger:
        row = ledger._db.execute("SELECT phase,finished FROM wechat_send_operations").fetchone()
        assert tuple(row) == ("INTERRUPTED", 1)
        assert ledger.action_summary(action.action_id)["state"] == "UNCONFIRMED"


def test_host_reader_start_failure_still_reaps_reader_without_native_access(tmp_path, monkeypatch):
    calls = []

    class Reader:
        async def start(self, *_args):
            calls.append("start")
            raise SocialError("synthetic_reader_failure")

        async def stop(self):
            calls.append("stop")
            return True

    monkeypatch.setattr(host, "LEDGER", tmp_path / "ledger.db")
    monkeypatch.setattr(host, "JsonReaderWorker", lambda *_: Reader())
    output = {}
    with pytest.raises(SocialError, match="reader_failure"):
        asyncio.run(host.trial(BINDING.account.window, "a" * 64, lambda *_: None,
                               SimpleNamespace(value_mode=False, deferred_focus=False), output))
    assert calls == ["start", "stop"]
    assert output["reader_reaped"] and output["worker_reaped"] and output["provider_restored"]
