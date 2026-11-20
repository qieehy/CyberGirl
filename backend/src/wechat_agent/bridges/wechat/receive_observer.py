"""Read-only receive observation; sender retains its original strict observer.

GetForegroundWindow may return NULL during deactivation (Microsoft Win32 docs).
NULL is known absence of a foreground window, not an unknown nonzero owner.
Only current-window NULL queries receive bounded accounting here. Foreground
event handles and every nonzero handle still use the original fail-closed
classification. No target event/sample is filtered. Never use this for writes.
"""
import time

from ...social.models import SocialError
from .background_observer import BackgroundObserver


class ReceiveObserver(BackgroundObserver):
    def __init__(self, target_pid, *, interval=0.01, clock=time.perf_counter):
        super().__init__(target_pid, interval=interval)
        self.clock = clock
        self.null_queries = 0
        self.null_since = None
        self.null_max = 0.0
        self.unknown_contexts = []

    def _finish_null(self):
        if self.null_since is not None:
            self.null_max = max(self.null_max, self.clock() - self.null_since)
            self.null_since = None
        if self.null_max > 0.25:
            self.failure = "receive_observer_no_foreground_stalled"

    def _classify_owner(self, hwnd, resolver, *, source):
        if not hwnd and source in {"sample", "event_current", "guard_current"}:
            self.null_queries += 1
            now = self.clock()
            if self.null_since is None:
                self.null_since = now
            self.null_max = max(self.null_max, now - self.null_since)
            if self.null_max > 0.25:
                self.failure = "receive_observer_no_foreground_stalled"
            return False
        if source != "event":
            self._finish_null()
        result = super()._classify_owner(hwnd, resolver, source=source)
        if result is None and len(self.unknown_contexts) < 16:
            self.unknown_contexts.append(dict(source=source, window_is_null=not bool(hwnd)))
        return result

    def check(self):
        result = super().check()
        if self.null_since is not None:
            self.null_max = max(self.null_max, self.clock() - self.null_since)
            if self.null_max > 0.25:
                self.failure = "receive_observer_no_foreground_stalled"
        if self.failure:
            raise SocialError(self.failure)
        return result

    def report(self):
        self._finish_null()
        return dict(super().report(), no_foreground_window_queries=self.null_queries,
                    maximum_no_foreground_ms=round(self.null_max * 1000, 3),
                    no_foreground_policy="read_only_known_null_at_most_250ms_nonzero_and_events_remain_strict",
                    unattributed_window_contexts=self.unknown_contexts)
