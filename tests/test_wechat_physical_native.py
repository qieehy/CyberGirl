"""Injected mouse candidate: all Windows IO mocked; no cursor or UI changes."""

import ctypes as C
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.wechat_native_worker import make_backend
from tests.test_wechat_native_sender import ACTION, BINDING
from tests.test_wechat_posted_native import NativeModel
from wechat_agent.bridges.wechat.directed_click import ClickResult, Point
from wechat_agent.bridges.wechat.physical_native import (
    PHYSICAL_MARKER,
    MousePacket,
    NativePhysicalTrial,
    click_packets,
)
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("point,desktop,expected", [
    (Point(0, 0), (0, 0, 1920, 1080), (0, 0)),
    (Point(1919, 1079), (0, 0, 1920, 1080), (65535, 65535)),
    (Point(-1920, -1080), (-1920, -1080, 3840, 2160), (0, 0)),
])
def test_exact_x64_packets_use_absolute_virtual_desktop_and_one_down_up_pair(point, desktop, expected):
    assert C.sizeof(MousePacket) == 40
    packets = click_packets(point, desktop)
    assert len(packets) == 2
    assert [(p.value.mi.dx, p.value.mi.dy) for p in packets] == [expected, expected]
    assert [p.value.mi.dwFlags for p in packets] == [0xC003, 0xC005]
    assert all(p.type == p.value.mi.mouseData == p.value.mi.time == p.value.mi.dwExtraInfo == 0 for p in packets)


@pytest.mark.parametrize("desktop", [(0, 0, 0, 1080), (0, 0, 1920, 1), (0, 0, 70000, 1080), (False, 0, 1920, 1080)])
def test_invalid_desktop_cannot_build_input(desktop):
    with pytest.raises(SocialError):
        click_packets(Point(100, 100), desktop)


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("complete", [True, False])
def test_single_batch_never_retries_and_partial_acceptance_remains_unknown(count, complete):
    model = NativeModel("complete")
    backend = model.backend
    backend.__class__ = NativePhysicalTrial
    calls = []
    backend._inject = lambda value: calls.append(value) or count

    def completion(token):
        if not complete:
            raise SocialError("physical_completion_missing")

    backend._wait_completion = completion
    result = backend.submit_once(backend.initial.input_token)
    assert len(calls) == 1 and result.message_mode == "injected"
    assert result.state == ("REQUESTED" if count == 2 and complete else "UNKNOWN")
    assert not result.cancel_attempted and not result.cancel_acknowledged
    assert model.events == ["CLICK_BEGIN_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"]
    with pytest.raises(SocialError, match="order"):
        backend.submit_once(backend.initial.input_token)


def test_guard_rejects_surface_change_before_any_sendinput():
    model = NativeModel("complete")
    backend = model.backend
    backend.__class__ = NativePhysicalTrial
    calls = []
    user = SimpleNamespace(GetSystemMetrics=lambda i: {76: 0, 77: 0, 78: 1920, 79: 1080}[i],
        SendInput=lambda *args: calls.append(args) or 2)
    backend.memory = SimpleNamespace(u=user)
    backend.messages = SimpleNamespace(validate_class=lambda: None, _foreground_and_hardware=lambda: None)
    backend._snapshot = lambda: (replace(model.before, button_token="c" * 64), dict(pressed_bit_set=False))
    with pytest.raises(SocialError):
        backend._inject_in_context(model.before)
    assert not calls


@pytest.mark.parametrize("fail", [False, True])
def test_thread_dpi_is_restored_even_when_input_guard_fails(fail):
    model = NativeModel("complete")
    backend = model.backend
    backend.__class__ = NativePhysicalTrial
    changes = []
    user = SimpleNamespace(SetThreadDpiAwarenessContext=lambda context: changes.append(context) or 11,
        GetThreadDpiAwarenessContext=lambda: 11, AreDpiAwarenessContextsEqual=lambda *args: True)
    backend.memory = SimpleNamespace(u=user)

    def inject(value):
        if fail:
            raise SocialError("synthetic_guard_failed")
        return 2

    backend._inject_in_context = inject
    if fail:
        with pytest.raises(SocialError, match="guard_failed"):
            backend._inject(model.before)
    else:
        assert backend._inject(model.before) == 2
    assert len(changes) == 2 and changes[-1] == 11


def test_factory_requires_explicit_physical_route_and_exact_marker():
    model = NativeModel("complete")
    backend = make_backend(BINDING.account.window, model.backend.fingerprint, physical_trial=True)
    assert type(backend) is NativePhysicalTrial and backend.memory is None
    backend.configure(BINDING, replace(ACTION, text=PHYSICAL_MARKER))
    with pytest.raises(SocialError, match="route_conflict"):
        make_backend(BINDING.account.window, model.backend.fingerprint, physical_trial=True, sequence_trial=True)


def test_injected_result_cannot_claim_window_ack_or_queued_move():
    with pytest.raises(SocialError, match="invalid_gesture_result"):
        ClickResult("REQUESTED", "bad_ack", True, True, False, True, message_mode="injected")


def test_injected_mode_is_durable_but_never_delivery_evidence(tmp_path):
    from tests.test_wechat_gesture_journal import NOW, setup
    from wechat_agent.bridges.wechat.contracts import Cursor
    from wechat_agent.bridges.wechat.send_state import SendState
    from wechat_agent.social.ledger import Ledger

    path = tmp_path / "injected.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        state, action_id = setup(ledger)
        action = ledger.claim(action_id)
        op = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(op, BINDING, action, "INPUT_PERMITTED")
        state.permit(op, BINDING, action, "SUBMIT_PERMITTED")
        for stage in ("CLICK_BEGIN_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"):
            state.gesture_intent(op, BINDING, action, stage)
        state.gesture_result(op, BINDING, action, ClickResult("REQUESTED", "injected_not_delivery", True, True, False, False, message_mode="injected"))
        assert state.gesture_summary(op)["message_mode"] == "injected"
        assert ledger.claim(action_id) is None
    with Ledger(path, now=lambda: NOW) as ledger:
        assert SendState(ledger).gesture_summary(op)["message_mode"] == "injected"
        assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
