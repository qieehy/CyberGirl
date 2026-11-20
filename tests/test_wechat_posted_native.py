"""Native queued emitter and completion checks with synthetic memory/UI only."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.wechat_native_worker import make_backend
from tests.test_wechat_directed_click import snapshot
from tests.test_wechat_directed_native import messages
from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat.directed_click import (
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
)
from wechat_agent.bridges.wechat.directed_native import TRIAL_MARKER, NativeDirectedTrial
from wechat_agent.bridges.wechat.native_sender import NativeSendBackend
from wechat_agent.bridges.wechat.posted_native import (
    POSTED_TRIAL_MARKER,
    BoundPostedMessages,
    NativePostedTrial,
)
from wechat_agent.social.models import OutgoingAction, SocialError


def poster():
    legacy, sent = messages()
    queued = []
    legacy.memory.u.PostMessageW = lambda *args: queued.append(args) or 1
    result = BoundPostedMessages(legacy.memory, legacy.window, legacy.expected_code)
    result.thread, result.point, result.verify_effect = 777, 1234, lambda message: None
    return result, queued, sent


@pytest.mark.parametrize("change", ["window", "thread", "point", "message", "class", "code", "foreground", "hardware", "missing_verifier"])
def test_post_guard_rejects_changes_before_any_queued_or_sync_call(change):
    instance, queued, sent = poster()
    window, thread, point, message = instance.window, instance.thread, instance.point, WM_MOUSEMOVE
    if change == "window":
        window = replace(window, hwnd=window.hwnd + 1)
    elif change == "thread":
        thread += 1
    elif change == "point":
        point += 1
    elif change == "message":
        message = 0x102
    elif change == "class":
        instance.memory.u.GetClassLongPtrW = lambda *args: 0
    elif change == "code":
        instance.memory.read_bytes = lambda *args: b"B" * 128
    elif change == "foreground":
        instance.memory.u.GetForegroundWindow = lambda: 0
    elif change == "hardware":
        instance.memory.u.GetAsyncKeyState = lambda key: 0x8000
    else:
        instance.verify_effect = None
    with pytest.raises(SocialError):
        instance.post(window, thread, message, 0, point)
    assert not queued and not sent


@pytest.mark.parametrize("change", ["generation", "class", "foreground", "hardware", "point"])
def test_binding_is_checked_again_after_effect_observer_returns(change):
    instance, queued, sent = poster()

    def mutate(message):
        if change == "generation":
            def fail():
                raise SocialError("synthetic_generation_changed")
            instance.memory.validate = fail
        elif change == "class":
            instance.memory.u.GetClassLongPtrW = lambda *args: 0
        elif change == "foreground":
            instance.memory.u.GetForegroundWindow = lambda: 0
        elif change == "hardware":
            instance.memory.u.GetAsyncKeyState = lambda key: 0x8000
        else:
            instance.point += 1

    instance.verify_effect = mutate
    with pytest.raises(SocialError):
        instance.post(instance.window, instance.thread, WM_MOUSEMOVE, 0, instance.point)
    assert not queued and not sent
    with pytest.raises(SocialError, match="order"):
        instance.post(instance.window, instance.thread, WM_MOUSEMOVE, 0, instance.point)


def test_post_sequence_is_single_use_and_cancel_stays_bound_after_foreground_changes():
    instance, queued, sent = poster()
    window, thread, point = instance.window, instance.thread, instance.point
    with pytest.raises(SocialError, match="order"):
        instance.post(window, thread, WM_LBUTTONUP, 0, point)
    assert instance.post(window, thread, WM_MOUSEMOVE, 0, point)
    assert instance.post(window, thread, WM_LBUTTONDOWN, 1, point)
    instance.memory.u.GetForegroundWindow = lambda: 0
    instance.memory.u.GetAsyncKeyState = lambda key: 0x8000
    assert instance.cancel(window, thread)
    for message, wparam in ((WM_LBUTTONUP, 0), (WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, 1)):
        with pytest.raises(SocialError, match="order"):
            instance.post(window, thread, message, wparam, point)
    with pytest.raises(SocialError, match="order"):
        instance.cancel(window, thread)
    with pytest.raises(SocialError, match="synchronous"):
        instance.emit(window, thread, WM_LBUTTONUP, 0, point, 100)
    assert [r[1] for r in queued] == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_CANCELMODE] and not sent


def test_failed_post_consumes_sequence_and_cannot_be_used_for_down():
    instance, queued, sent = poster()
    instance.memory.u.PostMessageW = lambda *args: queued.append(args) or 0
    assert not instance.post(instance.window, instance.thread, WM_MOUSEMOVE, 0, instance.point)
    with pytest.raises(SocialError, match="order"):
        instance.post(instance.window, instance.thread, WM_LBUTTONDOWN, 1, instance.point)
    assert len(queued) == 1 and not sent


class NativeModel:
    def __init__(self, outcome):
        self.outcome, self.now, self.down_at, self.up_at = outcome, 0.0, None, None
        self.events, self.transient = [], outcome == "transient_clear"
        fingerprint = hashlib.sha256(BINDING.account.account_id.encode()).hexdigest()
        self.backend = NativePostedTrial(BINDING.account.window, fingerprint,
            lambda binding: pytest.fail("real opener forbidden"), class_code=lambda: b"A" * 128,
            post_submit_timeout=0.4, clock=lambda: self.now, sleep=self.sleep)
        self.backend.configure(BINDING, OutgoingAction("posted-trial", BINDING.scope, "local-operator", POSTED_TRIAL_MARKER))
        self.before, initial = snapshot()
        self.before = replace(self.before, input=replace(self.before.input,
            draft_sha256=hashlib.sha256(POSTED_TRIAL_MARKER.encode()).hexdigest()))
        self.backend.initial, self.backend.input_used = initial, True
        self.backend._snapshot = self.surface
        self.backend.messages = SimpleNamespace(thread=None, point=None, verify_effect=None, post=self.post,
            cancel=lambda *args: self.events.append(WM_CANCELMODE) or True)
        self.backend.gesture_record = lambda stage: self.events.append(stage)
        self.backend.draft_diagnostics = self.draft

    def sleep(self, delay):
        self.now += delay

    def surface(self):
        after_up = self.up_at is not None
        elapsed = self.now - self.up_at if after_up else 0
        pressed = self.down_at is not None and self.now >= self.down_at + 0.1 and self.outcome != "press_missing"
        if after_up and elapsed >= 0.08 and self.outcome != "still_pressed":
            pressed = False
        empty = after_up and elapsed >= 0.12 and self.outcome != "uncleared"
        value = replace(self.before, input=replace(self.before.input, empty=empty,
            draft_sha256=hashlib.sha256(("" if empty else POSTED_TRIAL_MARKER).encode()).hexdigest()), enabled=not empty)
        if after_up and self.outcome == "foreign_editor":
            value = replace(value, input=replace(value.input, input_token="c" * 64))
        elif after_up and self.outcome == "foreign_draft":
            value = replace(value, input=replace(value.input, draft_sha256="c" * 64, empty=False))
        elif after_up and self.outcome == "foreign_button":
            value = replace(value, button_token="c" * 64)
        elif after_up and self.outcome == "capture_retained":
            value = replace(value, capture_hwnd=BINDING.account.window.hwnd)
        return value, {"pressed_bit_set": pressed}

    def post(self, window, thread, message, wparam, lparam):
        self.backend.messages.verify_effect(message)
        self.events.append(message)
        if message == WM_LBUTTONDOWN:
            self.down_at = self.now
        elif message == WM_LBUTTONUP:
            self.up_at = self.now
        return True

    def draft(self, **kwargs):
        assert kwargs["expected_token"] == self.backend.initial.input_token
        if self.transient and self.up_at is not None:
            self.transient = False
            raise SocialError("draft_changed_during_diagnostic")
        empty = self.surface()[0].input.empty
        return {"comparison": dict(value_empty=empty, text_status="unsupported" if self.outcome == "missing_pattern" else "read",
                                   text_empty=empty, exact_equal=self.outcome != "missing_pattern")}


@pytest.mark.parametrize("outcome", ["complete", "transient_clear", "uncleared", "still_pressed", "press_missing",
    "foreign_editor", "foreign_draft", "foreign_button", "capture_retained", "missing_pattern"])
def test_native_queued_completion_requires_same_editor_dual_empty_and_released_button(outcome):
    model = NativeModel(outcome)
    result = model.backend.submit_once(model.backend.initial.input_token)
    success = outcome in {"complete", "transient_clear"}
    assert result.state == ("REQUESTED" if success else "UNKNOWN")
    assert result.message_mode == "queued" and not result.cancel_acknowledged
    messages = [e for e in model.events if type(e) is int]
    assert messages == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_CANCELMODE if outcome == "press_missing" else WM_LBUTTONUP]
    if success:
        assert result.reason == "posted_requested_editor_empty_and_button_released"
        assert model.now >= model.up_at + 0.12
    with pytest.raises(SocialError, match="order"):
        model.backend.submit_once(model.backend.initial.input_token)


def test_factory_requires_explicit_distinct_route_and_rejects_old_marker_without_opening_native_io():
    fingerprint = hashlib.sha256(BINDING.account.account_id.encode()).hexdigest()
    default = make_backend(BINDING.account.window, fingerprint)
    directed = make_backend(BINDING.account.window, fingerprint, directed_trial=True)
    posted = make_backend(BINDING.account.window, fingerprint, posted_trial=True)
    assert type(default) is NativeSendBackend and type(directed) is NativeDirectedTrial and type(posted) is NativePostedTrial
    with pytest.raises(SocialError, match="route_conflict"):
        make_backend(BINDING.account.window, fingerprint, directed_trial=True, posted_trial=True)
    with pytest.raises(SocialError, match="marker_denied"):
        posted.configure(BINDING, OutgoingAction("old", BINDING.scope, "local-operator", TRIAL_MARKER))
    assert default.memory is None and directed.memory is None and posted.memory is None


@pytest.mark.parametrize("foreground_ready", [True, False])
def test_posted_focus_phase_never_activates_or_focuses_as_a_fallback(foreground_ready):
    model = NativeModel("complete")
    backend = model.backend
    backend.input_used = False
    backend.ui = SimpleNamespace(focus_once=lambda *args: pytest.fail("queued trial must not activate/focus"))
    backend.readiness_diagnostics = lambda: dict(account_and_filehelper_verified=True, both_draft_patterns_empty=True,
        same_editor_and_button=True, foreground_hit_and_focus=foreground_ready, no_capture_or_hardware_input=True,
        button_not_pressed=True, class_entry_matches_pinned_code=True)
    backend.inspect = lambda: backend.initial
    if foreground_ready:
        backend.prepare_input_focus()
        assert backend.focus_used
        with pytest.raises(SocialError, match="already_used"):
            backend.prepare_input_focus()
    else:
        with pytest.raises(SocialError, match="readiness_failed"):
            backend.prepare_input_focus()
