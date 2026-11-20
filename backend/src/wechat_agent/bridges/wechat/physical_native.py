"""Opt-in fixed-marker physical click, never a fallback or default sender.

Uses the standard Win32 SendInput batch rather than posted window messages.
The operator must explicitly approve moving the mouse for this trial.
"""

import ctypes as C
from dataclasses import replace

from ...social.models import SocialError
from .directed_click import ClickResult
from .directed_native import NativeDirectedTrial
from .posted_native import NativePostedTrial

PHYSICAL_MARKER = "W2B-20261107-physical-once"


class MouseInput(C.Structure):
    _fields_ = [("dx", C.c_int32), ("dy", C.c_int32), ("mouseData", C.c_uint32),
                ("dwFlags", C.c_uint32), ("time", C.c_uint32), ("dwExtraInfo", C.c_size_t)]


class InputUnion(C.Union):
    _fields_ = [("mi", MouseInput), ("padding", C.c_ubyte * 32)]


class MousePacket(C.Structure):
    _fields_ = [("type", C.c_uint32), ("value", InputUnion)]


def click_packets(point, desktop):
    left, top, width, height = desktop
    if (any(type(v) is not int for v in desktop) or not 2 <= width <= 65536 or not 2 <= height <= 65536
            or not left <= point.x < left + width or not top <= point.y < top + height):
        raise SocialError("physical_desktop_or_point_invalid")
    x = round((point.x - left) * 65535 / (width - 1))
    y = round((point.y - top) * 65535 / (height - 1))
    packets = (MousePacket * 2)()
    for packet, button in zip(packets, (0x2, 0x4), strict=True):
        packet.type = 0  # INPUT_MOUSE
        packet.value.mi = MouseInput(x, y, 0, 0x8000 | 0x4000 | 0x1 | button, 0, 0)
    return packets


class NativePhysicalTrial(NativePostedTrial):
    trial_marker = PHYSICAL_MARKER

    def prepare_input_focus(self):
        NativeDirectedTrial.prepare_input_focus(self)

    def readiness_diagnostics(self):
        return dict(super().readiness_diagnostics(), transport="SendInput", mouse_will_move=True,
                    eligibility="explicit_physical_click_trial")

    def _inject(self, snapshot):
        user = self.memory.u
        for name, args, result in (
            ("SetThreadDpiAwarenessContext", [C.c_void_p], C.c_void_p),
            ("GetThreadDpiAwarenessContext", [], C.c_void_p),
            ("AreDpiAwarenessContextsEqual", [C.c_void_p, C.c_void_p], C.c_int),
        ):
            getattr(user, name).argtypes, getattr(user, name).restype = args, result
        previous = user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not previous:
            raise SocialError("physical_dpi_context_unavailable")
        try:
            return self._inject_in_context(snapshot)
        finally:
            restored = user.SetThreadDpiAwarenessContext(previous)
            if not restored or not user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(), previous):
                raise SocialError("physical_dpi_restore_unverified")

    def _inject_in_context(self, snapshot):
        if C.sizeof(C.c_void_p) != 8 or C.sizeof(MousePacket) != 40:
            raise SocialError("physical_input_layout_unsupported")
        self.messages.thread = snapshot.receiver_thread
        self.messages.validate_class()
        user = self.memory.u
        user.GetSystemMetrics.argtypes, user.GetSystemMetrics.restype = [C.c_int], C.c_int
        user.SendInput.argtypes = [C.c_uint, C.POINTER(MousePacket), C.c_int]
        user.SendInput.restype = C.c_uint
        desktop = tuple(user.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        packets = click_packets(snapshot.button_bounds.center, desktop)
        # Revalidate the surface immediately before actual global input. If this
        # fails, no mouse movement, button event or alternate route is attempted.
        fresh, connection = self._snapshot()
        fresh.verify(self.binding, self.action, self.initial, previous=snapshot)
        if connection["pressed_bit_set"] or tuple(user.GetSystemMetrics(i) for i in (76, 77, 78, 79)) != desktop:
            raise SocialError("physical_surface_changed")
        self.messages.validate_class()
        self.messages._foreground_and_hardware()
        return int(user.SendInput(2, packets, C.sizeof(MousePacket)))

    def submit_once(self, token):
        if not self.input_used or self.submit_used or self.initial is None or self.gesture_record is None:
            raise SocialError("physical_submit_order_denied")
        self.submit_used = True
        if token != self.initial.input_token:
            raise SocialError("physical_editor_changed")
        attempted = up_intended = False
        try:
            for stage in ("CLICK_BEGIN_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"):
                self.gesture_record(stage)
                if stage == "CLICK_DOWN_INTENT":
                    attempted = True  # Conservative effect uncertainty after durable intent.
                if stage == "CLICK_UP_INTENT":
                    up_intended = True
            snapshot, connection = self._snapshot()
            snapshot.verify(self.binding, self.action, self.initial)
            if connection["pressed_bit_set"]:
                raise SocialError("physical_button_already_pressed")
            self.gesture_before = snapshot
            # Durable intentions are not a claim that Windows accepted events.
            count = self._inject(snapshot)
            if count != 2:
                raise SocialError("physical_batch_partial_or_rejected")
            result = ClickResult("REQUESTED", "physical_input_injected_not_delivery", True, True, False, False,
                                 message_mode="injected")
            self._wait_completion(token)
            return replace(result, reason="physical_requested_editor_empty_and_button_released")
        except Exception as exc:
            return ClickResult("UNKNOWN" if attempted else "NOT_STARTED",
                exc.code if isinstance(exc, SocialError) else "physical_injection_or_observation_failed",
                attempted, up_intended, False, False, message_mode="injected")
