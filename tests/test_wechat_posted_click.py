"""Upstream-derived queued gesture, synthetic observations and test-owned HWNDs."""

import asyncio
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_wechat_directed_click import snapshot
from tests.test_wechat_native_sender import ACTION, BINDING
from tests.wechat_click_receiver import OwnedPoster
from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.directed_click import (
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    ClickResult,
    pack_client_point,
)
from wechat_agent.bridges.wechat.posted_click import PostedClick, PostedObservation
from wechat_agent.bridges.wechat.send_process import KillJob, python_worker_command
from wechat_agent.social.models import SocialError


class QueueModel:
    def __init__(self, *, press_delay=0.1, fail_stage=None, fail_message=None, mutate_stage=None, press_missing=False):
        self.before, self.initial = snapshot()
        self.now, self.down_at, self.press_delay = 0.0, None, press_delay
        self.fail_stage, self.fail_message, self.mutate_stage = fail_stage, fail_message, mutate_stage
        self.press_missing, self.changed, self.events = press_missing, False, []

    def sleep(self, delay):
        assert delay >= 0
        self.events.append(("wait", delay))
        self.now += delay

    def observe(self):
        current = replace(self.before, receiver_thread=778) if self.changed else self.before
        pressed = self.down_at is not None and self.now >= self.down_at + self.press_delay and not self.press_missing
        return PostedObservation(current, pressed)

    def record(self, stage):
        if stage == self.fail_stage:
            raise OSError("synthetic commit failure")
        self.events.append(stage)
        if stage == self.mutate_stage:
            self.changed = True

    def post(self, window, thread, message, wparam, lparam):
        assert window == BINDING.account.window and thread == self.before.receiver_thread
        assert lparam == pack_client_point(self.before.client_point)
        assert wparam == (1 if message == WM_LBUTTONDOWN else 0)
        self.events.append(("post", message, self.now))
        if message == WM_LBUTTONDOWN:
            self.down_at = self.now
        return message != self.fail_message

    def cancel(self, window, thread):
        assert window == BINDING.account.window and thread == self.before.receiver_thread
        self.events.append("cancel_enqueued")
        return True

    def gesture(self):
        return PostedClick(BINDING, ACTION, self.initial, observe=self.observe, post=self.post,
            cancel=self.cancel, record=self.record, clock=lambda: self.now, sleep=self.sleep)

    def messages(self):
        return [e[1] for e in self.events if isinstance(e, tuple) and e[0] == "post"]


def test_upstream_move_down_wait_up_with_delayed_press_is_requested_only():
    model = QueueModel()
    gesture = model.gesture()
    result = gesture.perform_once()
    assert result.state == "REQUESTED" and result.message_mode == "queued"
    assert result.reason == "window_messages_enqueued_not_delivery"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    posts = [e for e in model.events if isinstance(e, tuple) and e[0] == "post"]
    assert posts[-1][2] - posts[-2][2] >= model.press_delay >= 0.07
    for message, stage in ((WM_MOUSEMOVE, "CLICK_MOVE_INTENT"), (WM_LBUTTONDOWN, "CLICK_DOWN_INTENT"), (WM_LBUTTONUP, "CLICK_UP_INTENT")):
        assert model.events.index(stage) < next(i for i, e in enumerate(model.events) if isinstance(e, tuple) and e[:2] == ("post", message))
    with pytest.raises(SocialError, match="already_used"):
        gesture.perform_once()


@pytest.mark.parametrize("stage,expected", [("CLICK_BEGIN_INTENT", []), ("CLICK_MOVE_INTENT", []),
    ("CLICK_DOWN_INTENT", [WM_MOUSEMOVE]), ("CLICK_UP_INTENT", [WM_MOUSEMOVE, WM_LBUTTONDOWN])])
def test_failed_durable_intent_cannot_reach_its_effect(stage, expected):
    model = QueueModel(fail_stage=stage)
    result = model.gesture().perform_once()
    assert model.messages() == expected
    assert result.state == ("UNKNOWN" if expected else "NOT_STARTED")
    assert result.cancel_attempted == (WM_LBUTTONDOWN in expected)
    assert not result.cancel_acknowledged


@pytest.mark.parametrize("message", [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP])
def test_post_failure_never_retries_and_cancel_is_only_enqueued(message):
    model = QueueModel(fail_message=message)
    result = model.gesture().perform_once()
    expected = [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    assert model.messages() == expected[:expected.index(message) + 1]
    assert result.state == "UNKNOWN" and result.move_attempted
    assert result.cancel_enqueued == (message != WM_MOUSEMOVE) and not result.cancel_acknowledged


@pytest.mark.parametrize("stage", ["CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"])
def test_changed_target_after_parent_ack_stops_before_the_next_post(stage):
    model = QueueModel(mutate_stage=stage)
    result = model.gesture().perform_once()
    assert result.state == "UNKNOWN"
    assert len(model.messages()) == ["CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"].index(stage)
    assert WM_LBUTTONUP not in model.messages()


def test_missing_press_keeps_up_blocked_and_queued_cleanup_is_not_rollback():
    model = QueueModel(press_missing=True)
    result = model.gesture().perform_once()
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN]
    assert result.state == "UNKNOWN" and result.reason == "posted_button_press_not_observed"
    assert result.cancel_enqueued and not result.cancel_acknowledged and not result.up_attempted
    assert 0.5 <= model.now < 0.6


def test_input_change_after_move_is_not_treated_as_no_effect():
    model = QueueModel()
    original = model.record

    def revoke(stage):
        original(stage)
        if stage == "CLICK_DOWN_INTENT":
            model.before = replace(model.before, input=replace(model.before.input, draft_sha256="c" * 64))

    model.record = revoke
    result = model.gesture().perform_once()
    assert model.messages() == [WM_MOUSEMOVE] and result.state == "UNKNOWN"
    assert result.down_attempted and result.cancel_enqueued  # Durable DOWN may outlive a child.


def test_pressed_state_lost_during_up_commit_cannot_reach_up():
    model = QueueModel()
    original = model.record

    def release(stage):
        original(stage)
        if stage == "CLICK_UP_INTENT":
            model.down_at = None

    model.record = release
    result = model.gesture().perform_once()
    assert result.state == "UNKNOWN" and result.reason == "posted_button_press_lost_before_up"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN] and result.cancel_enqueued


def test_stalled_clock_cannot_create_unbounded_press_polling():
    model = QueueModel(press_missing=True)
    reads = []
    gesture = PostedClick(BINDING, ACTION, model.initial,
        observe=lambda: reads.append(True) or model.observe(), post=model.post, cancel=model.cancel,
        record=model.record, clock=lambda: 0.0, sleep=lambda seconds: None)
    result = gesture.perform_once()
    assert result.state == "UNKNOWN" and len(reads) == 35  # Three pre-post reads + at most 32 polls.
    assert result.reason == "posted_press_poll_limit_reached"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN]


@pytest.mark.parametrize("pressed", [True, False])
@pytest.mark.parametrize("read_seconds", [0.42, 0.44, 0.6])
def test_slow_press_read_is_distinct_from_timely_false_samples(pressed, read_seconds):
    model = QueueModel(press_delay=0, press_missing=not pressed)
    original = model.observe

    def slow_read():
        if model.down_at is not None:
            model.now += read_seconds
        return original()

    model.observe = slow_read
    result = model.gesture().perform_once()
    timely_press = pressed and read_seconds == 0.42
    assert result.state == ("REQUESTED" if timely_press else "UNKNOWN")
    assert result.up_attempted is timely_press
    if timely_press:
        assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    else:
        assert result.reason == "posted_press_observation_overran_deadline"
        assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN]
        assert result.cancel_enqueued and not result.cancel_acknowledged


def test_scheduler_delay_exhausting_wait_does_not_claim_a_button_sample():
    model = QueueModel()
    original = model.sleep

    def delayed_sleep(seconds):
        original(seconds + 0.6)

    model.sleep = delayed_sleep
    result = model.gesture().perform_once()
    assert result.state == "UNKNOWN" and result.reason == "posted_press_wait_deadline_before_sample"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN]
    assert result.cancel_enqueued and not result.up_attempted


def test_late_observation_still_checks_target_identity_first():
    model = QueueModel(press_delay=0)
    original = model.observe

    def changed_read():
        if model.down_at is not None:
            model.now += 0.6
            model.changed = True
        return original()

    model.observe = changed_read
    result = model.gesture().perform_once()
    assert result.state == "UNKNOWN"
    assert result.reason not in {"posted_press_observation_overran_deadline", "posted_button_press_not_observed"}
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN] and result.cancel_enqueued


@pytest.mark.parametrize("fields", [dict(message_mode="queued", cancel_acknowledged=True),
    dict(move_attempted=True), dict(message_mode="queued", move_attempted=False), dict(message_mode=[])])
def test_result_cannot_claim_synchronous_ack_or_hide_queued_move(fields):
    values = dict(state="UNKNOWN", reason="synthetic", down_attempted=True, up_attempted=False,
                  cancel_attempted=True, cancel_acknowledged=False)
    values.update(fields)
    with pytest.raises(SocialError, match="invalid_gesture_result"):
        ClickResult(**values)


@pytest.mark.skipif(os.name != "nt", reason="Windows x64 owned queue fixture")
def test_real_owned_window_queue_accepts_posts_before_processing_and_cancel_is_still_pending():
    async def scenario():
        process = await asyncio.create_subprocess_exec(*python_worker_command(Path(__file__).with_name("wechat_click_receiver.py"), "queue-barrier"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        job = KillJob(process.pid)
        try:
            async with asyncio.timeout(5):
                data = json.loads(await process.stdout.readline())
            assert data["pid"] == process.pid
            window = WindowBinding(data["pid"], data["created_filetime"], data["hwnd"], "a" * 64, "b" * 64)
            point = 10 | (20 << 16)
            poster = OwnedPoster(window, data["thread"], point)
            for wrong in (replace(window, pid=window.pid + 1), replace(window, created_filetime=window.created_filetime + 1)):
                denied = OwnedPoster(wrong, data["thread"], point)
                with pytest.raises(SocialError, match="fixture_receiver"):
                    denied.post(wrong, data["thread"], WM_MOUSEMOVE, 0, point)
                assert not denied.calls
            assert poster.post(window, data["thread"], WM_MOUSEMOVE, 0, point)
            assert poster.post(window, data["thread"], WM_LBUTTONDOWN, 1, point)
            assert poster.cancel(window, data["thread"])
            # The receiver has NOT entered its message loop. All True results above are merely queued.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(process.stdout.readline(), 0.05)
            process.stdin.write(b"1")
            await process.stdin.drain()
            async with asyncio.timeout(3):
                received = [json.loads(await process.stdout.readline()) for _ in range(3)]
            assert [r["message"] for r in received] == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_CANCELMODE]
            assert [r["lparam"] for r in received] == [point, point, 0]
        finally:
            job.terminate()
            await process.wait()
            assert job.active() == 0
            job.close()
    asyncio.run(scenario())
