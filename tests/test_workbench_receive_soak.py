"""R1 acceptance must reject missing, repeated or unseparated source evidence."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from scripts.probe_wechat_workbench_receive_soak import require_markers, visible_payload_hashes
from wechat_agent.social.models import SocialError


def markers():
    return {label: dict(sha256=hashlib.sha256(f"R1-20261116-{label}".encode()).hexdigest(),
                        at_seconds=seconds, after_restart=after)
            for label, seconds, after in (("A", 10, False), ("B", 600, False), ("C", 920, True))}


def test_distinct_markers_span_restart():
    require_markers(markers(), True)


def test_legacy_records_without_saved_text_are_not_backfilled_or_hashed():
    state = dict(messages=[dict(message_id="legacy", text=None), dict(message_id="fresh", text="新文本")])
    assert visible_payload_hashes(state) == {"fresh": hashlib.sha256("新文本".encode()).hexdigest()}
    assert state["messages"][0]["text"] is None


@pytest.mark.parametrize("damage", ["missing", "duplicate", "no_restart", "c_before_restart", "a_after_restart", "too_close", "reversed"])
def test_incomplete_observation_cannot_pass(damage):
    seen = markers()
    if damage == "missing":
        seen.pop("B")
    elif damage == "duplicate":
        seen["second_a"] = dict(seen["A"])
    elif damage == "c_before_restart":
        seen["C"]["after_restart"] = False
    elif damage == "a_after_restart":
        seen["A"]["after_restart"] = True
    elif damage == "too_close":
        seen["B"]["at_seconds"] = 20
    elif damage == "reversed":
        seen["B"]["at_seconds"] = 950
    with pytest.raises(SocialError):
        require_markers(seen, damage != "no_restart")


@pytest.mark.parametrize("changed_cursor", [False, True])
def test_full_observation_restart_and_rejected_replacement_cleanup(tmp_path, monkeypatch, changed_cursor):
    from scripts import probe_wechat_workbench_receive_soak as probe
    from tests.test_wechat_background_control import BINDING
    from tests.test_workbench import seed
    from wechat_agent.bridges.wechat.contracts import Cursor, SourceRecord
    from wechat_agent.bridges.wechat.store import BridgeStore
    from wechat_agent.social.ledger import Ledger

    async def run():
        clock, next_marker, readers, events = [0.0], [0], [], []
        with Ledger(tmp_path / "observation.sqlite3") as ledger:
            seed(ledger)
            before = probe.fingerprints(ledger._db)
            store = BridgeStore(ledger)

            class Receiver:
                binding = BINDING

                def __init__(self):
                    self.worker = SimpleNamespace(process=SimpleNamespace(pid=100 + len(readers), returncode=None), rpc=self.rpc)
                    self.catalog = SimpleNamespace(checkpoints=lambda: {"cursor": next_marker[0],
                        "changed": changed_cursor and len(readers) > 1 and self is readers[-1]})
                    readers.append(self)

                async def rpc(self, command):
                    assert command == "read_metrics"
                    return dict(snapshot_retries=0, snapshot_retry_exhausted=0)

                async def poll(self):
                    for index, at in enumerate((10, 60, 110)):
                        if next_marker[0] == index and clock[0] >= at:
                            n = index + 2
                            record = SourceRecord(BINDING.account.account_id, BINDING.account.db_epoch,
                                "filehelper", "dm", "selected", n, n, BINDING.account.self_id,
                                ledger.now(), ledger.now(), "R1-20261116-" + "ABC"[index])
                            store.ingest(BINDING, "selected", Cursor(n - 1, n - 1), (record,))
                            next_marker[0] += 1
                    return dict(fresh=0, more=False)

                async def close(self):
                    self.worker.process.returncode = 0
                    store.unbind(BINDING)

            async def factory():
                store.bind(BINDING)
                return Receiver(), None

            class Server:
                def __init__(self, ledger, wb):
                    self.workbench = wb

                async def start(self, port):
                    pass

                async def aclose(self):
                    pass

            async def state(server):
                return server.workbench.snapshot()

            async def sleep(delta):
                clock[0] += delta
                await readers[-1].poll()

            def start(wb):
                wb.reading = True

            monkeypatch.setattr(probe, "WorkbenchConsoleServer", Server)
            monkeypatch.setattr(probe, "http_state", state)
            monkeypatch.setattr(probe.WeChatWorkbenchService, "start", start)
            observer = SimpleNamespace(start=lambda: None, check=lambda: None, stop=lambda: True,
                report=lambda: dict(failure=None, foreground_target_seen=False, maximum_sample_gap_ms=1))
            output = {}
            invocation = probe.observe(ledger, factory, observer, output,
                lambda kind, **data: events.append(kind), duration=180, clock=lambda: clock[0], sleep=sleep)
            if changed_cursor:
                with pytest.raises(SocialError, match="r1_restart_identity_or_cursor_changed"):
                    await invocation
                assert len(readers) == 2 and all(r.worker.process.returncode == 0 for r in readers)
            else:
                await invocation
                assert output["status"] == "r1_workbench_observation_completed"
                assert output["midrun_restart"]["at_seconds"] == 90
                assert output["marker_checks_passed"] and len(output["new_texts"]) == 3
                assert events.count("midrun_restart_completed") == 1
            assert output["all_readers_reaped"] and output["active_bindings"] == 0
            assert probe.fingerprints(ledger._db) == before
    asyncio.run(run())
