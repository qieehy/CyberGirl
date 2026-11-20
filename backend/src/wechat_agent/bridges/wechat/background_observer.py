"""Read-only foreground-event and pointer sampling for a bounded BG1 trial.

No titles, text, keys or screenshots are read. Event hooks are asynchronous;
pointer sampling reports its coverage, not proof against arbitrarily short
movement. All native resources belong to this observer thread.
"""

import ctypes as C
import math
import os
import threading
import time
from ctypes import wintypes

from ...social.models import SocialError


class BackgroundObserver:
    def __init__(self, target_pid, *, interval=0.01, allow_target_foreground=False):
        if type(target_pid) is not int or not 0 < target_pid < 2**32:
            raise ValueError("target PID required")
        if (type(interval) not in (int, float) or not math.isfinite(interval)
                or not 0.005 <= interval <= 0.05):
            raise ValueError("bounded observation interval required")
        self.pid, self.interval = target_pid, interval
        if type(allow_target_foreground) is not bool:
            raise ValueError("foreground policy must be boolean")
        self.allow_target_foreground = allow_target_foreground
        self.ready, self.stop_event = threading.Event(), threading.Event()
        self.thread = None
        self.failure = None
        self.target_seen = False
        self.events = self.samples = self.cursor_changes = 0
        self.max_gap = 0.0
        self.last_sample_at = None
        self.unhooked = False
        self.phase = "prepare"
        self.target_foreground_phases = []
        self.target_sources = []
        self.target_sampled = False
        self.target_events = []
        self.target_events_dropped = 0
        self.current_probe = None

    def _record_target(self, source):
        # An asynchronous event is deliberately still a stop signal even when
        # the later current-window query disagrees. Do not miss a short switch.
        self.target_seen = True
        if source not in self.target_sources:
            self.target_sources.append(source)
        if source != "event":
            self.target_sampled = True
        if len(self.target_foreground_phases) < 16 and (
                not self.target_foreground_phases or self.target_foreground_phases[-1] != self.phase):
            self.target_foreground_phases.append(self.phase)

    def _record_target_event(self, object_id, child_id, *, current_target, same_window):
        self._record_target("event")
        detail = dict(phase=self.phase, object_id=object_id, child_id=child_id,
                      window_self_event=object_id == 0 and child_id == 0,
                      current_foreground_is_target=current_target,
                      event_window_is_current=same_window)
        if len(self.target_events) < 16:
            self.target_events.append(detail)
        else:
            self.target_events_dropped += 1

    def set_phase(self, phase):
        if phase not in {"prepare", "value_input", "char_input", "pseudo_focus_start", "pseudo_focus_end",
                         "mouse_move", "mouse_down", "mouse_up", "return_down", "return_up"}:
            raise SocialError("background_observer_phase_denied")
        self.phase = phase

    def start(self):
        if self.thread is not None:
            raise SocialError("background_observer_already_used")
        if os.name != "nt":
            raise SocialError("background_windows_required")
        self.thread = threading.Thread(target=self._run, name="wechat-background-observation", daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            self.stop()
            raise SocialError("background_observer_start_timeout")
        try:
            self.check()
        except BaseException:
            self.stop()
            raise
        return self

    def _classify_owner(self, hwnd, resolver, *, source):
        pid = C.c_uint32()
        if not hwnd or not resolver(hwnd, C.byref(pid)):
            self.failure = "background_observer_foreground_unknown"
            return None
        return pid.value == self.pid

    def _run(self):
        hook = None
        try:
            user = C.WinDLL("user32", use_last_error=True)
            callback_type = C.WINFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_void_p,
                                         C.c_int32, C.c_int32, C.c_uint32, C.c_uint32)
            for name, args, result in (
                ("SetWinEventHook", [C.c_uint32, C.c_uint32, C.c_void_p, callback_type,
                                    C.c_uint32, C.c_uint32, C.c_uint32], C.c_void_p),
                ("UnhookWinEvent", [C.c_void_p], C.c_int),
                ("PeekMessageW", [C.POINTER(wintypes.MSG), C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32], C.c_int),
                ("TranslateMessage", [C.POINTER(wintypes.MSG)], C.c_int),
                ("DispatchMessageW", [C.POINTER(wintypes.MSG)], C.c_ssize_t),
                ("GetWindowThreadProcessId", [C.c_void_p, C.POINTER(C.c_uint32)], C.c_uint32),
                ("GetForegroundWindow", [], C.c_void_p),
                ("GetCursorPos", [C.POINTER(wintypes.POINT)], C.c_int),
            ):
                getattr(user, name).argtypes = args
                getattr(user, name).restype = result

            def owner_is_target(hwnd, *, source="event"):
                return self._classify_owner(hwnd, user.GetWindowThreadProcessId, source=source)

            def current_foreground(source):
                hwnd = user.GetForegroundWindow()
                target = owner_is_target(hwnd, source=source)
                if target:
                    self._record_target(source)
                return hwnd, target

            @callback_type
            def callback(_hook, _event, hwnd, object_id, child_id, _thread, _tick):
                try:
                    self.events += 1
                    if owner_is_target(hwnd):
                        current, target = current_foreground("event_current")
                        self._record_target_event(object_id, child_id,
                                                  current_target=target, same_window=hwnd == current)
                except BaseException:
                    self.failure = "background_observer_callback_failed"

            # EVENT_SYSTEM_FOREGROUND only; OUTOFCONTEXT requires this thread's pump.
            hook = user.SetWinEventHook(3, 3, None, callback, 0, 0, 0)
            if not hook:
                raise SocialError("background_observer_hook_failed")
            self.current_probe = lambda: current_foreground("guard_current")
            previous_point = previous_at = None
            while True:
                message = wintypes.MSG()
                for _ in range(128):
                    if not user.PeekMessageW(C.byref(message), None, 0, 0, 1):
                        break
                    user.TranslateMessage(C.byref(message))
                    user.DispatchMessageW(C.byref(message))
                else:
                    raise SocialError("background_observer_event_overflow")
                current_foreground("sample")
                point = wintypes.POINT()
                if not user.GetCursorPos(C.byref(point)):
                    raise SocialError("background_observer_cursor_unknown")
                now = time.perf_counter()
                if previous_at is not None:
                    self.max_gap = max(self.max_gap, now - previous_at)
                if previous_point is not None and previous_point != (point.x, point.y):
                    self.cursor_changes += 1
                previous_point, previous_at = (point.x, point.y), now
                self.last_sample_at = now
                self.samples += 1
                self.ready.set()
                if self.stop_event.is_set():
                    break  # Drain queued events and take a final sample before unhooking.
                self.stop_event.wait(self.interval)
        except BaseException as exc:
            self.failure = exc.code if isinstance(exc, SocialError) else "background_observer_failed"
        finally:
            if hook:
                self.unhooked = bool(user.UnhookWinEvent(hook))
                if not self.unhooked:
                    self.failure = "background_observer_unhook_failed"
            self.ready.set()

    @property
    def foreground_violation(self):
        return self.target_seen and not self.allow_target_foreground

    def check(self):
        if self.failure:
            raise SocialError(self.failure)
        if self.foreground_violation:
            raise SocialError("background_target_became_foreground")
        if (self.thread is None or not self.thread.is_alive() or not self.ready.is_set()
                or self.stop_event.is_set() or self.samples == 0):
            raise SocialError("background_observer_unavailable")
        if self.current_probe is not None:
            self.current_probe()
            if self.failure:
                raise SocialError(self.failure)
            if self.foreground_violation:
                raise SocialError("background_target_became_foreground")
        if (self.last_sample_at is None or time.perf_counter() - self.last_sample_at > 0.25
                or self.max_gap > 0.25):
            self.failure = "background_observer_sampling_stalled"
            raise SocialError(self.failure)
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(2)
        return self.thread is not None and not self.thread.is_alive() and self.unhooked

    def report(self):
        return dict(
            foreground_target_seen=self.target_seen, foreground_events=self.events,
            pointer_samples=self.samples, pointer_position_changes=self.cursor_changes,
            requested_sample_interval_ms=round(self.interval * 1000, 3),
            maximum_sample_gap_ms=round(self.max_gap * 1000, 3),
            pointer_change_attribution="not_determined", failure=self.failure,
            target_foreground_phases=list(self.target_foreground_phases),
            target_detection_sources=list(self.target_sources),
            target_foreground_sampled=self.target_sampled,
            target_foreground_events=[dict(event) for event in self.target_events],
            target_foreground_events_dropped=self.target_events_dropped,
            hook_released=self.unhooked,
            observer_stopped=self.thread is not None and not self.thread.is_alive(),
        )
