"""Short virtual-clock lifecycle tests never count as actual 30-minute evidence."""
import asyncio
from types import SimpleNamespace

import pytest

from scripts.probe_wechat_42013_receive_soak import receive_window, totals
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_background_sender import setup
from tests.test_wechat_sender import NoModel
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("damage", [None, "cursor", "foreground"])
def test_receive_only_lifecycle_restores_cursor_rejects_switch_and_never_replays(tmp_path, damage):
    async def run():
        with Ledger(tmp_path / "original.db") as ledger:
            aid, _, _, native_calls = setup(ledger)
            ledger.claim(aid)
            ledger.finish_delivery(aid, "CONFIRMED", "synthetic-phone")
            clock, created, closed = [0.0], [], []
            class Reader:
                binding = BINDING
                def __init__(self):
                    created.append(self)
                    self.worker = SimpleNamespace(process=SimpleNamespace(pid=len(created)), rpc=self.rpc)
                    self.catalog = SimpleNamespace(checkpoints=lambda: {"cursor": 10})
                async def rpc(self, command):
                    assert command == "read_metrics"
                    return dict(snapshot_retries=0, snapshot_retry_exhausted=0)
                async def start(self):
                    return {"cursor": 11 if damage == "cursor" and len(created) > 1 else 10}
                async def poll(self):
                    return dict(fresh=0, replay=0, pages=0, more=False)
                async def close(self):
                    closed.append(self)
                    return True
            class Observer:
                def start(self):
                    pass
                def check(self):
                    if damage == "foreground" and clock[0] >= 2:
                        raise SocialError("background_target_became_foreground")
                def stop(self):
                    return True
                def report(self):
                    return dict(foreground_target_seen=damage == "foreground" and clock[0] >= 2,
                                failure=None, maximum_sample_gap_ms=10)
            async def sleep(seconds):
                clock[0] += seconds
            out, events, initial = {}, [], totals(ledger)
            call = receive_window(ledger, Reader(), Observer(), [aid], out,
                lambda kind, **fields: events.append(kind), receiver_factory=Reader, no_model_factory=NoModel,
                duration=4, clock=lambda: clock[0], sleep=sleep)
            if damage:
                with pytest.raises(SocialError):
                    await call
            else:
                await call
                assert out["status"] == "read_only_receive_restart_completed"
                assert out["continuous_seconds"] == 4 and out["target_seconds"] == 4
                assert out["midrun_restart"]["checkpoints_unchanged"]
                assert events == ["read_only_receive_started", "reader_and_service_restarted"]
            assert totals(ledger) == initial and not native_calls
            assert out["reader_reaped"] and out["observer_stopped"] and out["new_sends"] == 0
            assert closed[-1] is created[-1]
    asyncio.run(run())
