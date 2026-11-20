"""Constrained control faults with synthetic native/UI facts; no WeChat access."""

import ctypes as C
import hashlib
import sqlite3
from contextlib import nullcontext
from ctypes import wintypes
from dataclasses import replace

import pytest

from scripts.wechat_background_control import (
    MARKER,
    WM_CHAR,
    WM_DOWN,
    WM_KILLFOCUS,
    WM_MOVE,
    WM_SETFOCUS,
    WM_UP,
    BackgroundControl,
)
from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat.sender import InputObservation
from wechat_agent.social.models import OutgoingAction, SocialError

ACTION = OutgoingAction("background-test", BINDING.scope, "synthetic-operator", MARKER)


class Rig:
    def __init__(self):
        self.now = 0.0
        self.body = ""
        self.payload = bytearray()
        self.token = "a" * 64
        self.xy = (100, 150)
        self.account = BINDING.account.account_id
        self.enabled = True
        self.valid = True
        self.iconic = False
        self.posts = []
        self.intents = []
        self.fail_record = None
        self.fail_post = None
        self.after_post = lambda *_: None
        self.cleanup_ok = True
        self.oversleep = 0

    def guard(self):
        return self.valid

    def observe(self):
        return (InputObservation(self.account, "filehelper", BINDING.account.window,
                                 "native_username", self.token, False, not self.body,
                                 hashlib.sha256(self.body.encode()).hexdigest()), self.enabled, self.xy)

    def record(self, stage):
        self.intents.append(stage)
        return stage != self.fail_record

    def post(self, message, wparam, lparam):
        self.posts.append((message, wparam, lparam))
        if message == WM_CHAR:
            self.payload += wparam.to_bytes(2, "little")
            self.body = self.payload.decode("utf-16-le", errors="replace")
        elif message == WM_UP:
            self.body = ""
        self.after_post(message)
        return message != self.fail_post

    def is_iconic(self, hwnd):
        assert hwnd == BINDING.account.window.hwnd
        return self.iconic

    def screen_to_client(self, hwnd, point):
        assert hwnd == BINDING.account.window.hwnd
        p = C.cast(point, C.POINTER(wintypes.POINT)).contents
        p.x -= 10
        p.y -= 20
        return True

    def expected_point(self, xy):
        x, y = xy[0] - 10, xy[1] - 20
        return (y << 16) | (x & 0xFFFF)

    def verify_cleanup(self):
        return self.cleanup_ok

    def sleep(self, seconds):
        self.now += seconds + self.oversleep

    def control(self, **kwargs):
        return BackgroundControl(BINDING, ACTION, observe=self.observe, guard=self.guard,
                                 transport=self, record=self.record, clock=lambda: self.now,
                                 sleep=self.sleep, **kwargs)


def run(rig):
    return rig.control().run_once(write_guard=nullcontext())


def test_unicode_once_in_background_never_claims_delivery():
    rig = Rig()
    control = rig.control()
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and result.focus_cleanup_verified
    assert not result.delivery_verified
    assert result.input_units_attempted == len(MARKER.encode("utf-16-le")) // 2
    assert rig.payload.decode("utf-16-le") == MARKER
    messages = [p[0] for p in rig.posts]
    assert messages == [WM_SETFOCUS] + [WM_CHAR] * result.input_units_attempted + [WM_MOVE, WM_DOWN, WM_UP, WM_KILLFOCUS]
    # No journal calls occur between MOVE/DOWN/UP.
    assert rig.intents[-4:] == ["MOVE_INTENT", "DOWN_INTENT", "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())


@pytest.mark.parametrize("condition", ["draft", "account", "guard", "iconic", "lock"])
def test_preflight_rejection_leaves_input_untouched(condition):
    rig = Rig()
    if condition == "draft":
        rig.body = "existing private draft"
    elif condition == "account":
        rig.account = "another-account"
    elif condition == "guard":
        rig.valid = False
    elif condition == "iconic":
        rig.iconic = True

    class RejectLock:
        def __enter__(self):
            raise SocialError("lock_unavailable")

        def __exit__(self, *_):
            pass

    control = rig.control()
    result = control.run_once(write_guard=RejectLock() if condition == "lock" else nullcontext())
    assert result.state == "NOT_STARTED"
    assert not rig.posts


@pytest.mark.parametrize("message", [WM_SETFOCUS, WM_CHAR, WM_MOVE, WM_DOWN, WM_UP, WM_KILLFOCUS])
def test_uncertain_post_never_retries_or_claims_success(message):
    rig = Rig()
    rig.fail_post = message
    result = run(rig)
    assert result.state == "UNKNOWN"
    assert not result.delivery_verified
    assert [p[0] for p in rig.posts].count(message) == 1


@pytest.mark.parametrize("stage", ["PSEUDO_FOCUS_INTENT", "CHAR_INTENT", "MOVE_INTENT", "DOWN_INTENT", "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"])
def test_failed_journal_never_posts_corresponding_effect(stage):
    rig = Rig()
    rig.fail_record = stage
    result = run(rig)
    assert result.state != "REQUESTED"
    messages = [p[0] for p in rig.posts]
    if stage == "CHAR_INTENT":
        assert WM_CHAR not in messages
    elif stage in {"MOVE_INTENT", "DOWN_INTENT", "UP_INTENT"}:
        assert all(m not in messages for m in (WM_MOVE, WM_DOWN, WM_UP))
    elif stage == "PSEUDO_FOCUS_INTENT":
        assert WM_SETFOCUS not in messages
    else:
        assert WM_KILLFOCUS not in messages


@pytest.mark.parametrize("change", ["binding", "editor", "draft", "geometry", "disabled"])
def test_change_during_input_prevents_submit_preserves_draft(change):
    rig = Rig()

    def changed(message):
        if message != WM_CHAR:
            return
        if change == "binding":
            rig.valid = False
        elif change == "editor":
            rig.token = "b" * 64
        elif change == "draft":
            rig.body = "unexpected draft"
            rig.payload = bytearray(rig.body.encode("utf-16-le"))
        elif change == "geometry":
            rig.xy = (110, 150)
        else:
            rig.enabled = False

    rig.after_post = changed
    result = run(rig)
    assert result.state == "UNKNOWN"
    assert not any(p[0] in (WM_MOVE, WM_DOWN, WM_UP) for p in rig.posts)
    assert rig.body


def test_deadline_and_cleanup_failure_are_unknown():
    rig = Rig()
    rig.oversleep = 25
    result = run(rig)
    assert result.state == "UNKNOWN" and not result.focus_cleanup_verified
    assert not any(p[0] == WM_CHAR for p in rig.posts)
    rig = Rig()
    rig.cleanup_ok = False
    result = run(rig)
    assert result.state == "UNKNOWN" and not result.focus_cleanup_verified


def test_lock_release_failure_cannot_be_requested():
    rig = Rig()

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise SocialError("lock_cleanup_failed")

    result = rig.control().run_once(write_guard=Lock())
    assert result.state == "UNKNOWN" and result.reason == "lock_cleanup_failed"


def test_persistent_intent_failure_after_restart_blocks_new_effects(tmp_path):
    """A host can attach an existing durable single-use permit; never reissue it."""
    path = tmp_path / "intents.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE used (stage TEXT PRIMARY KEY)")

    def record(stage):
        try:
            with db:
                db.execute("INSERT INTO used VALUES (?)", (stage,))
            return True
        except sqlite3.IntegrityError:
            return False

    rig = Rig()
    rig.record = record
    assert run(rig).state == "UNKNOWN"  # repeated CHAR_INTENT is deliberately refused
    db.close()
    db = sqlite3.connect(path)
    rig2 = Rig()
    rig2.record = record
    assert run(rig2).state == "UNKNOWN"
    assert not rig2.posts
    db.close()


def test_only_fixed_control_marker_is_accepted():
    rig = Rig()
    with pytest.raises(SocialError, match="marker_denied"):
        BackgroundControl(BINDING, replace(ACTION, text="different"), observe=rig.observe,
                          guard=rig.guard, transport=rig, record=rig.record)


def test_enqueued_characters_can_outlive_native_guard_and_enter_a_later_target():
    """An admission counterexample, not an assertion of safe target binding.

    PostMessage acknowledges enqueue, so a successful pre/post native guard
    cannot bind the conversation at the later time Qt processes WM_CHAR.
    The constrained control refuses submit, but cannot retract queued input.
    """
    rig = Rig()
    pending = []
    original_post = rig.post

    def queue(message, wparam, lparam):
        if message == WM_CHAR:
            pending.append(wparam)
            rig.posts.append((message, wparam, lparam))
            return True
        return original_post(message, wparam, lparam)

    rig.post = queue
    result = run(rig)
    assert result.state == "UNKNOWN" and not result.delivery_verified
    assert not any(p[0] in (WM_MOVE, WM_DOWN, WM_UP) for p in rig.posts)
    assert pending  # No target change has been observed by the sender yet.
    # The application now changes its conversation and then processes the queue.
    later_target = "other-chat"
    later_draft = b"".join(unit.to_bytes(2, "little") for unit in pending).decode("utf-16-le")
    assert later_target != "filehelper" and later_draft == MARKER
