"""Bound HWND guards and explicit trial semantics, no real application effects."""

import ctypes as C
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_wechat_directed_click import snapshot
from tests.test_wechat_native_sender import BINDING
from wechat_agent.bridges.wechat.directed_click import WM_CANCELMODE, WM_LBUTTONDOWN, WM_LBUTTONUP
from wechat_agent.bridges.wechat.directed_native import (
    CLASS_ENTRY_RVA,
    TRIAL_MARKER,
    BoundWindowMessages,
    NativeDirectedTrial,
)
from wechat_agent.social.models import OutgoingAction, SocialError


def messages():
    sent = []
    window = BINDING.account.window
    base = 0x180000000

    def owner(hwnd, pointer):
        C.cast(pointer, C.POINTER(C.c_uint32)).contents.value = window.pid
        return 777

    user = SimpleNamespace(GetWindowThreadProcessId=owner, GetClassLongPtrW=lambda *args: base + CLASS_ENTRY_RVA,
        GetForegroundWindow=lambda: window.hwnd, GetAsyncKeyState=lambda key: 0,
        SendMessageTimeoutW=lambda *args: sent.append(args[:6]) or 1)
    memory = SimpleNamespace(u=user, base=base, validate=lambda: None, read_bytes=lambda p, n: b"A" * n)
    instance = object.__new__(BoundWindowMessages)
    instance.memory, instance.window, instance.expected_code = memory, window, b"A" * 128
    instance.thread, instance.point, instance.calls = 777, 1234, []
    instance.verify_effect = lambda message: None
    return instance, sent


@pytest.mark.parametrize("change", ["window", "thread", "parameters", "class", "foreground", "hardware"])
def test_emitter_rejects_target_or_state_change_before_any_native_message(change):
    emitter, sent = messages()
    window, thread, wparam = emitter.window, emitter.thread, 1
    if change == "window":
        window = replace(window, hwnd=window.hwnd + 1)
    elif change == "thread":
        thread += 1
    elif change == "parameters":
        wparam = 0
    elif change == "class":
        emitter.memory.u.GetClassLongPtrW = lambda *args: 0xFFFF000000000001
    elif change == "foreground":
        emitter.memory.u.GetForegroundWindow = lambda: 9999
    else:
        emitter.memory.u.GetAsyncKeyState = lambda key: 0x8000
    with pytest.raises(SocialError):
        emitter.emit(window, thread, WM_LBUTTONDOWN, wparam, emitter.point, 150)
    assert sent == []


def test_emitter_uses_bound_hwnd_and_bounded_flags_and_cleanup_ignores_foreground():
    emitter, sent = messages()
    assert emitter.emit(emitter.window, 777, WM_LBUTTONDOWN, 1, 1234, 150)
    assert emitter.emit(emitter.window, 777, WM_LBUTTONUP, 0, 1234, 150)
    emitter.memory.u.GetForegroundWindow = lambda: 9999
    assert emitter.cancel(emitter.window, 777, 150)
    assert [row[1] for row in sent] == [WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE]
    assert all(row[0] == emitter.window.hwnd and row[4:] == (0x23, 150) for row in sent)


@pytest.mark.parametrize("outcome", ["complete", "target_changed", "press_missing"])
def test_explicit_trial_rechecks_state_and_never_returns_delivery_confirmation(outcome):
    action = OutgoingAction("trial", BINDING.scope, "operator-source", TRIAL_MARKER)
    backend = NativeDirectedTrial(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                 lambda binding: pytest.fail("unexpected real opener"), class_code=lambda: b"A" * 128)
    backend.configure(BINDING, action)
    before, initial = snapshot()
    before = replace(before, input=replace(before.input, draft_sha256=hashlib.sha256(TRIAL_MARKER.encode()).hexdigest()))
    backend.initial, backend.input_used = initial, True
    pressed, events = False, []

    def surface():
        value = replace(before, button_token="c" * 64) if pressed and outcome == "target_changed" else before
        return value, dict(stable=True, connections=1, same_input_view=True, pressed_bit_set=pressed)

    def emit(window, thread, message, wparam, lparam, timeout):
        nonlocal pressed
        backend.messages.verify_effect(message)
        events.append(message)
        pressed = message == WM_LBUTTONDOWN and outcome != "press_missing"
        return True

    backend._snapshot = surface
    backend.messages = SimpleNamespace(thread=None, point=None, verify_effect=None, emit=emit,
        cancel=lambda *args: events.append(WM_CANCELMODE) or True)
    backend.gesture_record = lambda stage: None
    backend.draft_diagnostics = lambda **kwargs: {"comparison": dict(value_empty=True, text_status="read", text_empty=True, exact_equal=True)}
    result = backend.submit_once(initial.input_token)
    assert result.state == ("REQUESTED" if outcome == "complete" else "UNKNOWN")
    assert events == ([WM_LBUTTONDOWN, WM_LBUTTONUP] if outcome == "complete" else [WM_LBUTTONDOWN, WM_CANCELMODE])
    if outcome == "press_missing":
        assert result.reason == "directed_button_press_missing_after_down"
        assert result.cancel_acknowledged and not result.up_attempted
    with pytest.raises(SocialError):
        backend.submit_once(initial.input_token)


@pytest.mark.parametrize("identity_ready", [True, False])
def test_focus_preparation_requires_native_empty_identity_before_bound_element_effect(identity_ready):
    action = OutgoingAction("trial", BINDING.scope, "operator-source", TRIAL_MARKER)
    backend = NativeDirectedTrial(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                 lambda binding: pytest.fail("unexpected real opener"), class_code=lambda: b"A" * 128)
    backend.configure(BINDING, action)
    _, initial = snapshot()
    calls, focused = [], False

    def focus(token):
        nonlocal focused
        calls.append(token)
        focused = True

    backend.ui = SimpleNamespace(focus_once=focus)
    backend.inspect = lambda: replace(initial, focused=focused)
    backend.readiness_diagnostics = lambda: dict(account_and_filehelper_verified=identity_ready,
        both_draft_patterns_empty=True, same_editor_and_button=True, no_capture_or_hardware_input=True,
        button_not_pressed=True, class_entry_matches_pinned_code=True, foreground_hit_and_focus=focused)
    if identity_ready:
        backend.prepare_input_focus()
        assert calls == [initial.input_token]
        with pytest.raises(SocialError, match="already_used"):
            backend.prepare_input_focus()
    else:
        with pytest.raises(SocialError, match="prerequisites_failed"):
            backend.prepare_input_focus()
        assert calls == []
