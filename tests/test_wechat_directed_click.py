"""Candidate gesture checks; real messages go only to our hidden test window."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path, PureWindowsPath

import pytest

from tests.test_wechat_native_sender import ACTION, BINDING
from tests.wechat_click_receiver import OwnedEmitter
from wechat_agent.bridges.wechat.contracts import Cursor, WindowBinding
from wechat_agent.bridges.wechat.directed_click import (
    MK_LBUTTON,
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    ClickSnapshot,
    DirectedClick,
    Point,
    Rect,
    pack_client_point,
)
from wechat_agent.bridges.wechat.send_process import KillJob, python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import InputObservation
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def snapshot(binding=BINDING):
    value = InputObservation(binding.account.account_id, "filehelper", binding.account.window, "native_username",
                             "a" * 64, True, False, hashlib.sha256(ACTION.text.encode()).hexdigest())
    initial = replace(value, empty=True, draft_sha256=hashlib.sha256(b"").hexdigest())
    return ClickSnapshot(value, "b" * 64, Rect(500, 400, 600, 440), Rect(0, 0, 640, 480), Point(550, 420),
                         144, "physical_per_monitor_v2", binding.account.window.hwnd, binding.account.window.pid, 777,
                         binding.account.window.hwnd, binding.account.window.hwnd, binding.account.window.pid,
                         "b" * 64, 0, "chat_input", True, True, False), initial


def candidate(observations, *, emit=None, record=None, cancel=None, binding=BINDING):
    first, initial = snapshot(binding)
    events = []
    observations = iter(observations)

    def log(stage):
        events.append(stage)
        if record:
            record(stage)

    def send(window, thread, message, wparam, lparam, timeout):
        assert window == binding.account.window and thread == first.receiver_thread
        events.append((message, wparam, lparam, timeout))
        return emit(message, wparam, lparam, timeout) if emit else True

    def cleanup(window, thread, timeout):
        assert window == binding.account.window and thread == first.receiver_thread
        events.append("cancel")
        return cancel(window, thread, timeout) if cancel else True

    return DirectedClick(binding, ACTION, initial, observe=lambda: next(observations), emit=send,
                         cancel=cleanup, record=log), events


def test_single_pair_requires_owned_focus_and_reuses_the_bound_client_point():
    before, _ = snapshot()
    after = replace(before, input=replace(before.input, focused=False), focus_kind="send_button", capture_hwnd=before.receiver_hwnd)
    gesture, events = candidate([before, after])
    result = gesture.perform_once()
    point = pack_client_point(before.client_point)
    assert result.state == "REQUESTED" and not result.cancel_attempted
    assert events == ["CLICK_BEGIN_INTENT", "CLICK_DOWN_INTENT", (WM_LBUTTONDOWN, MK_LBUTTON, point, 150),
                      "CLICK_UP_INTENT", (WM_LBUTTONUP, 0, point, 150)]
    with pytest.raises(SocialError, match="directed_click_already_used"):
        gesture.perform_once()


@pytest.mark.parametrize("field,value", [
    ("receiver_hwnd", 999), ("receiver_pid", 999), ("receiver_thread", 0), ("foreground_hwnd", 999),
    ("hit_root_hwnd", 999), ("hit_pid", 999), ("hit_button_token", "c" * 64), ("capture_hwnd", 999),
    ("focus_kind", "other"), ("enabled", False), ("visible", False), ("input_busy", True),
    ("coordinate_space", "logical"), ("dpi", 0), ("client_point", Point(551, 420)),
    ("button_bounds", Rect(600, 400, 700, 440)),
])
def test_bad_identity_route_geometry_or_interference_has_zero_messages(field, value):
    before, _ = snapshot()
    gesture, events = candidate([replace(before, **{field: value})])
    result = gesture.perform_once()
    assert result.state == "NOT_STARTED" and not result.down_attempted and events == ["CLICK_BEGIN_INTENT"]


@pytest.mark.parametrize("change", ["account", "conversation", "draft", "input_control"])
def test_native_input_changes_cannot_reach_mouse_down(change):
    before, _ = snapshot()
    values = {"account": dict(account_id="other"), "conversation": dict(username="other"),
              "draft": dict(draft_sha256="c" * 64), "input_control": dict(input_token="c" * 64)}
    gesture, events = candidate([replace(before, input=replace(before.input, **values[change]))])
    assert gesture.perform_once().state == "NOT_STARTED" and events == ["CLICK_BEGIN_INTENT"]


@pytest.mark.parametrize("field,value", [("receiver_hwnd", 999), ("button_token", "c" * 64),
    ("button_bounds", Rect(499, 400, 599, 440)), ("dpi", 192), ("receiver_thread", 778), ("focus_kind", "other")])
def test_change_after_down_cancels_original_receiver_without_releasing_to_new_target(field, value):
    before, _ = snapshot()
    gesture, events = candidate([before, replace(before, **{field: value})])
    result = gesture.perform_once()
    assert result.state == "UNKNOWN" and result.down_attempted and not result.up_attempted
    assert result.cancel_attempted and result.cancel_acknowledged
    assert len([event for event in events if isinstance(event, tuple)]) == 1
    assert events[-2:] == ["CLICK_CANCEL_INTENT", "cancel"]


@pytest.mark.parametrize("stage", [WM_LBUTTONDOWN, WM_LBUTTONUP])
def test_timeout_is_unknown_even_if_cancellation_is_acknowledged(stage):
    before, _ = snapshot()
    gesture, events = candidate([before, before], emit=lambda message, *args: message != stage)
    result = gesture.perform_once()
    assert result.state == "UNKNOWN" and result.cancel_acknowledged
    assert result.up_attempted == (stage == WM_LBUTTONUP)
    with pytest.raises(SocialError):
        gesture.perform_once()


@pytest.mark.parametrize("stage", ["CLICK_DOWN_INTENT", "CLICK_UP_INTENT"])
def test_journal_failure_prevents_next_effect_and_cleanup_still_runs(stage):
    before, _ = snapshot()

    def fail(event):
        if event == stage or event == "CLICK_CANCEL_INTENT":
            raise OSError("synthetic journal failure")

    gesture, events = candidate([before, before], record=fail)
    result = gesture.perform_once()
    assert not result.up_attempted
    assert result.state == ("NOT_STARTED" if stage == "CLICK_DOWN_INTENT" else "UNKNOWN")
    assert result.cancel_attempted == (stage == "CLICK_UP_INTENT")


def test_interrupt_after_partial_down_attempt_cleans_up_then_propagates():
    before, _ = snapshot()

    def interrupt(*args):
        raise KeyboardInterrupt

    gesture, events = candidate([before], emit=interrupt)
    with pytest.raises(KeyboardInterrupt):
        gesture.perform_once()
    assert events[-1] == "cancel" and not any(isinstance(e, tuple) and e[0] == WM_LBUTTONUP for e in events)


def test_negative_monitor_origin_fractional_button_bounds_and_lparam_limits():
    before, initial = snapshot()
    physical = replace(before, button_bounds=Rect(-1000.5, 600.25, -900.25, 640.5),
                       client_bounds=Rect(-1500, 100, -700, 700), client_point=Point(549, 520))
    physical.verify(BINDING, ACTION, initial)
    assert pack_client_point(Point(32767, 32767)) == 0x7FFF7FFF
    for point in (Point(-1, 0), Point(0, 32768)):
        with pytest.raises(SocialError):
            pack_client_point(point)
    with pytest.raises(SocialError):
        Rect(0, 0, float("nan"), 10)


@pytest.mark.skipif(os.name != "nt", reason="Windows owned receiver fixture")
@pytest.mark.parametrize("mode", ["normal", "hang-down", "hang-up"])
def test_real_hwnd_messages_are_bounded_and_only_target_a_hidden_owned_receiver(mode, tmp_path):
    async def run():
        fixture = Path(__file__).with_name("wechat_click_receiver.py")
        literal = "space 'quote' $value `tick and\nnewline"
        process = await asyncio.create_subprocess_exec(*python_worker_command(fixture, mode, literal),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        job = KillJob(process.pid)
        try:
            async with asyncio.timeout(5):
                data = json.loads(await process.stdout.readline())
            assert data["pid"] == process.pid
            assert PureWindowsPath(data["environment_prefix"]) == PureWindowsPath(sys.prefix)
            assert PureWindowsPath(data["python_executable"]) == PureWindowsPath(sys._base_executable)
            assert data["versions"] == {name: version(name) for name in ("ai-agent", "cryptography")}
            assert data["arguments"] == [mode, literal]
            window = WindowBinding(data["pid"], data["created_filetime"], data["hwnd"], "a" * 64, "b" * 64)
            binding = replace(BINDING, account=replace(BINDING.account, window=window))
            before, initial = snapshot(binding)
            before = replace(before, receiver_thread=data["thread"])
            emitter = OwnedEmitter(window, data["thread"])
            for wrong in (replace(window, created_filetime=window.created_filetime + 1), replace(window, pid=window.pid + 1)):
                foreign = OwnedEmitter(wrong, data["thread"])
                with pytest.raises(SocialError, match="fixture_receiver"):
                    foreign.emit(wrong, data["thread"], WM_LBUTTONDOWN, 1, 0, 100)
                assert not foreign.calls
            with Ledger(tmp_path / "owned-gesture.db") as ledger:
                BridgeStore(ledger).bind(binding, mode="draft")
                ledger.configure_owner(binding.scope, binding.account.self_id)
                state = SendState(ledger)
                action_id = state.queue_marker(binding, "owned-window-gesture", ACTION.text)
                action = ledger.claim(action_id)
                operation = state.begin(binding, action, {"fixture": Cursor(0, 0)})
                state.permit(operation, binding, action, "INPUT_PERMITTED")
                state.permit(operation, binding, action, "SUBMIT_PERMITTED")

                def persist(stage):
                    state.gesture_intent(operation, binding, action, stage)
                    # A second connection must see the intention BEFORE emitter runs.
                    import sqlite3

                    with sqlite3.connect(ledger.path.as_uri() + "?mode=ro", uri=True) as check:
                        assert check.execute("SELECT stage FROM wechat_send_journal ORDER BY seq DESC LIMIT 1").fetchone()[0] == stage

                candidate_click = DirectedClick(binding, action, initial, observe=lambda: before, emit=emitter.emit,
                    cancel=emitter.cancel, record=persist, message_timeout_ms=100)
                result = candidate_click.perform_once()
                state.gesture_result(operation, binding, action, result)
                assert state.gesture_summary(operation)["state"] == result.state
                assert ledger.action_summary(action_id)["state"] == "SENDING"  # No delivery confirmation.
            if mode == "normal":
                assert result.state == "REQUESTED"
                async with asyncio.timeout(2):
                    messages = [json.loads(await process.stdout.readline()) for _ in range(2)]
                assert [row["message"] for row in messages] == [WM_LBUTTONDOWN, WM_LBUTTONUP]
                assert all(row["lparam"] == pack_client_point(before.client_point) for row in messages)
            else:
                assert result.state == "UNKNOWN" and result.down_attempted
                assert result.up_attempted == (mode == "hang-up")
                assert result.cancel_attempted and not result.cancel_acknowledged
            assert all(message in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE) for message, _, _ in emitter.calls)
        finally:
            job.terminate()
            async with asyncio.timeout(2):
                await process.wait()
            assert job.active() == 0
            job.close()

    asyncio.run(run())
