"""Synthetic input/geometry worker for parent-owned durable gesture tests."""

import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from wechat_agent.bridges.wechat.directed_click import (
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    ClickSnapshot,
    DirectedClick,
    Point,
    Rect,
)
from wechat_agent.bridges.wechat.posted_click import PostedClick, PostedObservation
from wechat_agent.bridges.wechat.posted_sequence import PostedSequence
from wechat_agent.bridges.wechat.send_worker import serve
from wechat_agent.bridges.wechat.sender import InputObservation

MODE, TRACE = sys.argv[1], Path(sys.argv[2])


def trace(stage):
    with TRACE.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"stage": stage}) + "\n")


class Backend:
    def set_gesture_recorder(self, record):
        self.record = record

    def configure(self, binding, action):
        self.binding, self.action, self.has_text = binding, action, False
        self.pressed = False
        self.initial = InputObservation(binding.account.account_id, "filehelper", binding.account.window,
            "native_username", "a" * 64, True, True, hashlib.sha256(b"").hexdigest())

    def prepare_input_focus(self):
        pass

    def inspect(self):
        return replace(self.initial, empty=not self.has_text,
            draft_sha256=hashlib.sha256((self.action.text if self.has_text else "").encode()).hexdigest())

    def input_text(self, text, token):
        self.has_text = True

    def observe(self):
        trace("observe")
        window = self.binding.account.window
        return ClickSnapshot(self.inspect(), "b" * 64, Rect(500, 400, 600, 440), Rect(0, 0, 640, 480), Point(550, 420),
            144, "physical_per_monitor_v2", window.hwnd, window.pid, 777, window.hwnd,
            window.hwnd, window.pid, "b" * 64, 0, "chat_input", True, True, False)

    def emit(self, window, thread, message, wparam, lparam, timeout):
        trace("down" if message == WM_LBUTTONDOWN else "up")
        if MODE == "hang-down" and message == WM_LBUTTONDOWN:
            time.sleep(30)
        if (MODE == "down-fail" and message == WM_LBUTTONDOWN
                or MODE == "up-fail" and message == WM_LBUTTONUP):
            return False
        return True

    def cancel(self, *args):
        trace("cancel")
        return True

    def posted_observe(self):
        return PostedObservation(self.observe(), self.pressed)

    def post(self, window, thread, message, wparam, lparam):
        trace({WM_MOUSEMOVE: "move", WM_LBUTTONDOWN: "down", WM_LBUTTONUP: "up"}[message])
        if MODE in {"posted-hang-move", "sequence-hang-move"} and message == WM_MOUSEMOVE:
            time.sleep(30)
        if (MODE in {"posted-move-fail", "sequence-move-fail"} and message == WM_MOUSEMOVE
                or MODE in {"posted-up-fail", "sequence-up-fail"} and message == WM_LBUTTONUP):
            return False
        if message == WM_LBUTTONDOWN:
            self.pressed = MODE != "posted-press-missing"
        return True

    def submit_once(self, token):
        if MODE.startswith("sequence-"):
            now = [0.0]
            return PostedSequence(self.binding, self.action, self.initial, observe=self.posted_observe,
                post=self.post, cancel=self.cancel, record=self.record,
                clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)).perform_once()
        if MODE.startswith("posted-"):
            return PostedClick(self.binding, self.action, self.initial, observe=self.posted_observe,
                post=self.post, cancel=self.cancel, record=self.record, press_timeout=0.15).perform_once()
        return DirectedClick(self.binding, self.action, self.initial, observe=self.observe,
            emit=self.emit, cancel=self.cancel, record=self.record).perform_once()


serve(Backend(), sys.stdin.buffer, sys.stdout.buffer)
