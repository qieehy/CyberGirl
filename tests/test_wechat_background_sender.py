"""Service integration boundaries; synthetic workers only."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_wechat_background_completion import clean_result
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_sender import NoModel
from wechat_agent.bridges.wechat.background_contract import INTEGRATED_MARKER, RESTART_MARKER
from wechat_agent.bridges.wechat.background_sender import WeChatBackgroundAdapter
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.service import SocialService


class Worker:
    def __init__(self, log, *, bad_ready=False, hang=False, reaped=True, pause=None):
        self.log, self.bad_ready, self.hang, self.reaped, self.pause = log, bad_ready, hang, reaped, pause
        self.entered = asyncio.Event()

    async def start(self, operation, binding, action):
        self.log.append("start")
        self.action = action

    async def _rpc(self, command):
        self.log.append(command)
        if command == "prepare":
            names = {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                     "standalone_identity_verified", "background_observer_active", "no_capture",
                     "button_not_pressed", "class_entry_matches_pinned_code", "covered_by_other_app"}
            return {"readiness": {k: not self.bad_ready for k in names}}
        stages = ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * len(self.action.text)
        for stage in stages:
            self.record(stage)
        if self.hang:
            self.entered.set()
            await asyncio.Event().wait()
        if self.pause:
            self.pause()
        # Both durable acknowledgements precede any simulated key effect.
        for stage in ["RETURN_DOWN_INTENT", "RETURN_UP_INTENT"]:
            self.record(stage)
        self.log.append("keys")
        self.record("PSEUDO_FOCUS_END_INTENT")
        return clean_result(self.action.text)

    async def stop(self):
        self.log.append("stop")
        return self.reaped


def setup(ledger, *, marker=INTEGRATED_MARKER, bad_lock=False, admission=True, **worker_args):
    log = []
    BridgeStore(ledger).bind(BINDING, mode="draft")
    ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
    action_id = SendState(ledger).queue_marker(BINDING, "integration", marker)
    worker = Worker(log, **worker_args)
    def factory(*, record):
        worker.record = record
        return worker
    def locks(_binding):
        log.append("lock")
        return None if bad_lock else SimpleNamespace(close=lambda: log.append("unlock"))
    def lease(window, record):
        return SimpleNamespace(open=lambda: log.append("open"), close=lambda **kw: log.append("restore" if kw["restore"] else "retain") or kw["restore"])
    async def baseline(binding):
        log.append("baseline")
        return {"selected": Cursor(0, 0)}
    adapter = WeChatBackgroundAdapter(ledger, BINDING, worker_factory=factory, lease_factory=lease,
        baseline_provider=baseline, writer_lock_factory=locks, admission_checker=lambda *_: admission)
    return action_id, adapter, worker, log


@pytest.mark.parametrize("marker", [INTEGRATED_MARKER, RESTART_MARKER])
def test_original_service_records_unknown_once_and_never_replays(tmp_path, marker):
    async def run():
        path = tmp_path / "ledger.db"
        with Ledger(path) as ledger:
            action_id, adapter, _, log = setup(ledger, marker=marker)
            async with SocialService(ledger, NoModel(), [adapter], dispatch_timeout=25) as service:
                assert (await service.dispatch(action_id))["state"] == "UNCONFIRMED"
                assert (await service.dispatch(action_id))["state"] == "UNCONFIRMED"
            assert log == ["baseline", "lock", "open", "start", "prepare", "run_background", "keys", "stop", "restore", "unlock"]
            assert adapter.last_run["clean_control_recorded"]
            assert adapter.last_run["mode"] == ("restarted_background_bmp" if marker == RESTART_MARKER else "integrated_background_bmp")
            row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
            assert row["input_permitted"] == row["submit_permitted"] == row["finished"] == 1
        with Ledger(path) as ledger:
            async with SocialService(ledger, NoModel(), [adapter], dispatch_timeout=25) as service:
                await service.dispatch(action_id)
            assert log.count("keys") == 1
    asyncio.run(run())


@pytest.mark.parametrize("options,expected", [({"admission": False}, []), ({"bad_lock": True}, ["baseline", "lock"]),
    ({"bad_ready": True}, ["baseline", "lock", "open", "start", "prepare", "stop", "restore", "unlock"])])
def test_preinput_rejections_have_no_permits(tmp_path, options, expected):
    async def run():
        with Ledger(tmp_path / "ledger.db") as ledger:
            action_id, adapter, _, log = setup(ledger, **options)
            async with SocialService(ledger, NoModel(), [adapter], dispatch_timeout=25) as service:
                assert (await service.dispatch(action_id))["state"] == "FAILED"
            assert log == expected
            assert not ledger._db.execute("SELECT 1 FROM wechat_send_operations WHERE input_permitted=1").fetchone()
    asyncio.run(run())


def test_admission_is_checked_again_under_writer_lock(tmp_path):
    async def run():
        with Ledger(tmp_path / "ledger.db") as ledger:
            aid, adapter, _, log = setup(ledger)
            checks = iter([True, False])
            adapter.admission_checker = lambda *_: next(checks)
            action = ledger.claim(aid)
            receipt = await adapter.send_text(action)
            assert receipt.status.value == "NOT_SUBMITTED"
            assert log == ["baseline", "lock", "unlock"]
    asyncio.run(run())


@pytest.mark.parametrize("text", ["中文🙂", "bad\nline", "\ud800"])
def test_unsupported_text_rejects_before_source_or_native_access(tmp_path, text):
    async def run():
        with Ledger(tmp_path / "ledger.db") as ledger:
            aid, adapter, _, log = setup(ledger)
            await adapter.send_text(replace(ledger.claim(aid), text=text))
            assert not log
    asyncio.run(run())


@pytest.mark.parametrize("reaped", [True, False])
def test_cancellation_after_input_reaps_before_releasing_and_never_submits(tmp_path, reaped):
    async def run():
        with Ledger(tmp_path / "ledger.db") as ledger:
            aid, adapter, worker, log = setup(ledger, hang=True, reaped=reaped)
            task = asyncio.create_task(adapter.send_text(ledger.claim(aid)))
            await worker.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert "keys" not in log
            assert log[-3:] == ["stop", "restore", "unlock"] if reaped else log[-2:] == ["stop", "retain"]
            assert adapter._disabled
            row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
            assert row["input_permitted"] == 1 and row["submit_permitted"] == 0 and row["finished"] == 1
            assert (adapter._held_locks is not None) == (not reaped)
    asyncio.run(run())
