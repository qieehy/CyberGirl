"""Explicit native wiring for the upstream-derived queued click candidate.

No default registration, fallback, queue-drain claim or delivery confirmation.
The fixed new marker is a proposed trial body, not authorization to send it.
"""

import ctypes as C
import time
from dataclasses import replace

from ...social.models import SocialError
from .click_geometry import geometry_report
from .directed_click import (
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    pack_client_point,
)
from .directed_native import BoundWindowMessages, NativeDirectedTrial
from .posted_click import PostedClick, PostedObservation

POSTED_TRIAL_MARKER = "W2B-20261104-posted-once"


class BoundPostedMessages(BoundWindowMessages):
    def __init__(self, memory, window, expected_code):
        super().__init__(memory, window, expected_code)
        self.attempted, self.enqueued, self.failed = [], set(), False
        self.memory.u.PostMessageW.argtypes = [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t]
        self.memory.u.PostMessageW.restype = C.c_int

    def emit(self, *args):
        raise SocialError("queued_synchronous_message_denied")

    def _foreground_and_hardware(self):
        if self.memory.u.GetForegroundWindow() != self.window.hwnd or any(
                self.memory.u.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 5, 6, 16, 17, 18, 91, 92)):
            raise SocialError("posted_foreground_or_hardware_changed")

    def post(self, window, thread, message, wparam, lparam):
        if (window != self.window or type(thread) is not int or thread <= 0 or thread != self.thread
                or type(message) is not int or message not in {WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE}
                or type(wparam) is not int or type(lparam) is not int):
            raise SocialError("posted_message_denied")
        expected = (0, 0) if message == WM_CANCELMODE else (1 if message == WM_LBUTTONDOWN else 0, self.point)
        if ((wparam, lparam) != expected or message != WM_CANCELMODE and
                (type(self.point) is not int or not 0 <= self.point <= 0x7FFF7FFF or self.point & 0x80008000)):
            raise SocialError("posted_parameters_changed")
        sequence = {WM_MOUSEMOVE: [], WM_LBUTTONDOWN: [WM_MOUSEMOVE], WM_LBUTTONUP: [WM_MOUSEMOVE, WM_LBUTTONDOWN]}
        if message == WM_CANCELMODE:
            if not self.attempted or WM_CANCELMODE in self.attempted:
                raise SocialError("posted_cancel_order_denied")
        elif (self.failed or self.attempted != sequence[message]
                or any(stage not in self.enqueued for stage in self.attempted)):
            raise SocialError("posted_message_order_denied")
        self.attempted.append(message)  # Even a rejected guard must not enable a retry.
        try:
            self.validate_class()
            if message != WM_CANCELMODE:
                self._foreground_and_hardware()
                if not callable(self.verify_effect):
                    raise SocialError("posted_effect_verifier_missing")
                self.verify_effect(message)
                if window != self.window or thread != self.thread or lparam != self.point:
                    raise SocialError("posted_binding_changed_during_observation")
                # The observation/IPC calls above can take time: bind again at the actual post boundary.
                self.validate_class()
                self._foreground_and_hardware()
            self.calls.append(message)
            C.set_last_error(0)
            accepted = bool(self.memory.u.PostMessageW(window.hwnd, message, wparam, lparam))
            if accepted:
                self.enqueued.add(message)
            else:
                self.failed = True
            return accepted
        except BaseException:
            self.failed = True
            raise

    def cancel(self, window, thread):
        # Only the original receiver is checked; a changed page/foreground must not redirect cleanup.
        return self.post(window, thread, WM_CANCELMODE, 0, 0)


class NativePostedTrial(NativeDirectedTrial):
    trial_marker = POSTED_TRIAL_MARKER

    def __init__(self, *args, clock=time.monotonic, sleep=time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock, self.sleep = clock, sleep

    def _make_messages(self):
        return BoundPostedMessages(self.memory, self.window, self.class_code())

    def _snapshot(self):
        started = self.clock()
        result = super()._snapshot()
        self.snapshot_observation_ms = round((self.clock() - started) * 1000)
        return result

    def readiness_diagnostics(self):
        result = super().readiness_diagnostics()
        return dict(result, transport="PostMessageW", eligibility="bounded_posted_class_trial",
                    snapshot_observation_ms=self.snapshot_observation_ms,
                    snapshot_phases_ms=self.snapshot_phases_ms, queue_drain_verified=False)

    def prepare_input_focus(self):
        # The queued trial uses manual foreground preparation; never inherit activation as a fallback.
        if self.focus_used or self.input_used:
            raise SocialError("native_focus_already_used")
        self._require_submit_path()
        self.inspect().verify(self.binding, self.action)
        self.focus_used = True

    def _completion_surface(self, snapshot, token):
        snapshot.input.verify(self.binding, self.action, require_focus=False,
                              initial=None if snapshot.input.empty else self.initial)
        fields = ("button_token", "button_bounds", "client_bounds", "client_point", "dpi", "coordinate_space",
                  "receiver_hwnd", "receiver_pid", "receiver_thread")
        if (snapshot.input.input_token != token or any(getattr(snapshot, key) != getattr(self.gesture_before, key) for key in fields)):
            raise SocialError("posted_completion_surface_changed")
        checks = geometry_report(snapshot)["checks"]
        if not all(value for key, value in checks.items() if key not in {"button_enabled", "input_focus_owned", "no_capture"}):
            raise SocialError("posted_completion_route_changed")
        if (snapshot.capture_hwnd not in {0, self.window.hwnd}
                or snapshot.input.focused and snapshot.focus_kind != "chat_input"
                or not snapshot.input.focused and snapshot.focus_kind != "send_button"):
            raise SocialError("posted_completion_focus_or_capture_changed")
        return snapshot.capture_hwnd == 0

    def _wait_completion(self, token):
        deadline = self.clock() + self.post_submit_timeout
        for _ in range(8):
            if self.clock() > deadline:
                break
            try:
                before, first_connection = self._snapshot()
                first_released = self._completion_surface(before, token)
                comparison = self.draft_diagnostics(expected_token=token)["comparison"]
                after, last_connection = self._snapshot()
                last_released = self._completion_surface(after, token)
                # A natural clear/focus transition can straddle the reads. Only stable empty reads pass.
                if (self.clock() <= deadline and before.input.empty and after.input.empty
                        and comparison["value_empty"] and comparison["text_status"] == "read"
                        and comparison["text_empty"] and comparison["exact_equal"]
                        and not first_connection["pressed_bit_set"] and not last_connection["pressed_bit_set"]
                        and first_released and last_released):
                    return
            except SocialError as exc:
                if exc.code not in {"directed_surface_changed", "draft_changed_during_diagnostic"}:
                    raise
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.sleep(min(0.05, remaining))
        raise SocialError("posted_completion_not_verified")

    def submit_once(self, token):
        if not self.input_used or self.submit_used or self.initial is None or self.gesture_record is None:
            raise SocialError("posted_submit_order_denied")
        self.submit_used = True
        if token != self.initial.input_token:
            raise SocialError("posted_editor_changed")

        def observe():
            snapshot, connection = self._snapshot()
            if self.gesture_before is None:
                self.gesture_before = snapshot
                self.messages.thread = snapshot.receiver_thread
                self.messages.point = pack_client_point(snapshot.client_point)
                self.messages.verify_effect = self._verify_effect
            return PostedObservation(snapshot, connection["pressed_bit_set"])

        # Lazy callbacks let BEGIN/preflight fail cleanly before the first native opener.
        result = PostedClick(self.binding, self.action, self.initial, observe=observe,
            post=lambda *args: self.messages.post(*args), cancel=lambda *args: self.messages.cancel(*args),
            record=self.gesture_record, clock=self.clock, sleep=self.sleep).perform_once()
        if result.state != "REQUESTED":
            return result
        try:
            self._wait_completion(token)
            return replace(result, reason="posted_requested_editor_empty_and_button_released")
        except Exception as exc:
            return replace(result, state="UNKNOWN", reason=exc.code if isinstance(exc, SocialError)
                           else "posted_completion_observation_failed")
