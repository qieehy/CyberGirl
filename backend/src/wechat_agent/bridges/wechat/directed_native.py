"""Explicit fixed-marker HWND trial. No default sender registration.

Eligibility relies on the documented HWND delivery API and observed class,
native page, hit target and current signal. Per-window subclassing remains
unobserved; only one bounded trial and subsequent echo can prove submission.
"""

import ctypes as C
import time

from ...social.models import SocialError
from .click_geometry import geometry_report, parse_geometry
from .directed_click import (
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    ClickResult,
    DirectedClick,
    pack_client_point,
)
from .native_sender import NativeSendBackend
from .qt_connections import inspect_clicked_connection

CLASS_ENTRY_RVA = 0x26B04F0
TRIAL_MARKER = "W2B-20261104-directed-once"


class BoundWindowMessages:
    def __init__(self, memory, window, expected_code):
        self.memory, self.window, self.expected_code = memory, window, expected_code
        self.thread = self.point = self.verify_effect = None
        self.calls = []
        user = memory.u
        for name, args, returns in (
            ("GetClassLongPtrW", [C.c_void_p, C.c_int], C.c_size_t),
            ("GetForegroundWindow", [], C.c_void_p),
            ("GetAsyncKeyState", [C.c_int], C.c_short),
            ("SendMessageTimeoutW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t,
                C.c_uint, C.c_uint, C.POINTER(C.c_size_t)], C.c_ssize_t),
        ):
            getattr(user, name).argtypes, getattr(user, name).restype = args, returns

    def validate_class(self):
        self.memory.validate()
        user = self.memory.u
        owner = C.c_uint32()
        thread = user.GetWindowThreadProcessId(self.window.hwnd, C.byref(owner))
        if owner.value != self.window.pid or not thread or self.thread is not None and thread != self.thread:
            raise SocialError("directed_receiver_changed")
        first = user.GetClassLongPtrW(self.window.hwnd, -24)
        if first != self.memory.base + CLASS_ENTRY_RVA or len(self.expected_code) != 128:
            raise SocialError("directed_class_entry_unverified")
        if self.memory.read_bytes(first, 128) != self.expected_code:
            raise SocialError("directed_class_code_changed")
        if user.GetClassLongPtrW(self.window.hwnd, -24) != first:
            raise SocialError("directed_class_changed")
        return thread

    def emit(self, window, thread, message, wparam, lparam, timeout):
        if (window != self.window or thread != self.thread or type(timeout) is not int or not 1 <= timeout <= 250
                or message not in {WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE}):
            raise SocialError("directed_message_denied")
        expected = (1, self.point) if message == WM_LBUTTONDOWN else (0, self.point) if message == WM_LBUTTONUP else (0, 0)
        if (wparam, lparam) != expected:
            raise SocialError("directed_parameters_changed")
        self.validate_class()
        if message != WM_CANCELMODE:
            if self.memory.u.GetForegroundWindow() != window.hwnd or any(
                    self.memory.u.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 5, 6, 16, 17, 18, 91, 92)):
                raise SocialError("directed_foreground_or_hardware_changed")
            self.verify_effect(message)  # Recheck after parent commit / pipe roundtrip.
        value = C.c_size_t()
        self.calls.append(message)
        C.set_last_error(0)
        return bool(self.memory.u.SendMessageTimeoutW(window.hwnd, message, wparam, lparam,
                                                      0x23, timeout, C.byref(value)))

    def cancel(self, window, thread, timeout):
        return self.emit(window, thread, WM_CANCELMODE, 0, 0, timeout)


class NativeDirectedTrial(NativeSendBackend):
    trial_marker = TRIAL_MARKER

    def __init__(self, *args, class_code, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_code = class_code
        self.messages = None
        self.gesture_record = None
        self.gesture_before = None
        self.down_returned = False

    def configure(self, binding, action):
        super().configure(binding, action)
        if action.text != self.trial_marker:
            raise SocialError("directed_trial_marker_denied")

    def set_gesture_recorder(self, callback):
        self.gesture_record = callback

    def _make_messages(self):
        return BoundWindowMessages(self.memory, self.window, self.class_code())

    def _snapshot(self):
        clock = getattr(self, "clock", time.monotonic)
        started = clock()
        first = self.inspect()
        inspected = clock()
        if self.messages is None:
            self.messages = self._make_messages()
        self.messages.validate_class()
        validated = clock()
        snapshot = parse_geometry(self.ui.click_geometry(first.input_token), first)
        geometry = clock()
        connection = inspect_clicked_connection(self.memory, self.runtime_ids["button"], self.runtime_ids["chat_input"])
        connected = clock()
        current = self.inspect()
        finished = clock()
        if (current.input_token != first.input_token or current.draft_sha256 != first.draft_sha256
                or current.empty != first.empty or current.focused != first.focused):
            raise SocialError("directed_surface_changed")
        if not connection["stable"] or connection["connections"] != 1 or not connection["same_input_view"]:
            raise SocialError("directed_connection_changed")
        # Durations only: never export runtime IDs, pointers, or draft contents.
        self.snapshot_phases_ms = dict(
            initial_identity=round((inspected - started) * 1000, 3),
            class_validation=round((validated - inspected) * 1000, 3),
            geometry=round((geometry - validated) * 1000, 3),
            connection_and_press=round((connected - geometry) * 1000, 3),
            final_identity=round((finished - connected) * 1000, 3),
            press_read_window_start=round((geometry - started) * 1000, 3),
            press_read_window_end=round((connected - started) * 1000, 3),
            total=round((finished - started) * 1000, 3))
        return snapshot, connection

    def readiness_diagnostics(self):
        snapshot, connection = self._snapshot()
        draft = self.draft_diagnostics()["comparison"]
        report = geometry_report(snapshot)
        spatial = all(v for k, v in report["checks"].items() if k != "button_enabled")
        both_empty = (draft["value_empty"] and draft["text_status"] == "read"
                      and draft["text_empty"] and draft["exact_equal"])
        return dict(account_and_filehelper_verified=True, both_draft_patterns_empty=both_empty,
            same_editor_and_button=True, foreground_hit_and_focus=spatial,
            no_capture_or_hardware_input=report["checks"]["no_capture"] and report["checks"]["no_hardware_interference"],
            button_not_pressed=not connection["pressed_bit_set"], class_entry_matches_pinned_code=True,
            per_window_procedure_observed=False, eligibility="bounded_hwnd_class_trial",
            submission_enabled=False, class_function_rva=CLASS_ENTRY_RVA)

    def _require_submit_path(self):
        report = self.readiness_diagnostics()
        required = ("account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                    "foreground_hit_and_focus", "no_capture_or_hardware_input", "button_not_pressed", "class_entry_matches_pinned_code")
        if not all(report[k] is True for k in required):
            raise SocialError("directed_trial_readiness_failed")

    def prepare_input_focus(self):
        if self.focus_used or self.input_used:
            raise SocialError("native_focus_already_used")
        report = self.readiness_diagnostics()
        required = ("account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                    "no_capture_or_hardware_input", "button_not_pressed", "class_entry_matches_pinned_code")
        if not all(report[k] is True for k in required):
            raise SocialError("directed_focus_prerequisites_failed")
        current = self.inspect()
        current.verify(self.binding, self.action, require_focus=False)
        self.focus_used = True
        if not report["foreground_hit_and_focus"]:
            self.ui.focus_once(current.input_token)  # Bound element; Windows may refuse foreground activation.
        self._require_submit_path()

    def _observe_gesture(self):
        snapshot, connection = self._snapshot()
        self._verify_pressed(connection, self.down_returned)
        return snapshot

    @staticmethod
    def _verify_pressed(connection, expected):
        if connection["pressed_bit_set"] is not expected:
            raise SocialError("directed_button_press_missing_after_down" if expected
                              else "directed_button_pressed_before_down")

    def _verify_effect(self, message):
        snapshot, connection = self._snapshot()
        after_down = message == WM_LBUTTONUP
        snapshot.verify(self.binding, self.action, self.initial, after_down=after_down,
                        previous=self.gesture_before)
        self._verify_pressed(connection, after_down)

    def submit_once(self, token):
        if not self.input_used or self.submit_used or self.gesture_record is None:
            raise SocialError("directed_submit_order_denied")
        self.submit_used = True
        if token != self.initial.input_token:
            raise SocialError("directed_editor_changed")

        def observe():
            snapshot = self._observe_gesture()
            if self.gesture_before is None:
                self.gesture_before = snapshot
                self.messages.thread = snapshot.receiver_thread
                self.messages.point = pack_client_point(snapshot.client_point)
                self.messages.verify_effect = self._verify_effect
            return snapshot

        def emit(window, thread, message, wparam, lparam, timeout):
            result = self.messages.emit(window, thread, message, wparam, lparam, timeout)
            if message == WM_LBUTTONDOWN and result:
                self.down_returned = True
            return result

        result = DirectedClick(self.binding, self.action, self.initial, observe=observe, emit=emit,
            cancel=self.messages.cancel, record=self.gesture_record).perform_once()
        if result.state == "REQUESTED":
            draft = self.draft_diagnostics(expected_token=token)["comparison"]
            if not (draft["value_empty"] and draft["text_status"] == "read" and draft["text_empty"] and draft["exact_equal"]):
                return ClickResult("UNKNOWN", "directed_editor_not_verified_empty", True, True, False, False)
            return ClickResult("REQUESTED", "directed_requested_and_editor_empty", True, True, False, False)
        return result
