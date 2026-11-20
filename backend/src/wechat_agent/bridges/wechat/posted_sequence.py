"""Explicit complete-gesture candidate based on the pinned MIT upstream.

Source: Banan1cee/whale_gril 0af4dcd5596192e0fa4d3938ea93b404771d6e02,
bridge/bg_sender.py:421-446; copyright/permission in assets/licenses.
All three effect intents are committed before one fresh preflight. The injected
post must validate the original native receiver at each call. No UIA calls or
parent roundtrips interrupt MOVE/DOWN/settle/UP. No delivery claim or retry.
"""

import math
import time

from ...social.models import SocialError
from .directed_click import (
    MK_LBUTTON,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    ClickResult,
    pack_client_point,
)
from .posted_click import PostedObservation


class PostedSequence:
    def __init__(self, binding, action, initial, *, observe, post, cancel, record,
                 settle=0.07, clock=time.perf_counter, sleep=time.sleep):
        if type(settle) not in (int, float) or not math.isfinite(settle) or not 0.07 <= settle <= 0.25:
            raise ValueError("sequence settle must be bounded")
        initial.verify(binding, action)
        self.binding, self.action, self.initial = binding, action, initial
        self.observe, self.post, self.cancel, self.record = observe, post, cancel, record
        self.settle, self.clock, self.sleep, self.used = settle, clock, sleep, False

    def perform_once(self):
        if self.used:
            raise SocialError("posted_sequence_already_used")
        self.used = True
        move = down = up = cancelled = enqueued = False
        thread = down_at = None
        try:
            self.record("CLICK_BEGIN_INTENT")
            # Intent is conservative: a killed child may have attempted its effect.
            for stage in ("CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"):
                self.record(stage)
                if stage == "CLICK_MOVE_INTENT":
                    move = True
            before = self.observe()
            if not isinstance(before, PostedObservation) or before.pressed:
                raise SocialError("posted_sequence_preflight_invalid")
            before.snapshot.verify(self.binding, self.action, self.initial)
            thread = before.snapshot.receiver_thread
            point = pack_client_point(before.snapshot.client_point)
            window = self.binding.account.window
            for message, wparam in ((WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0)):
                if message == WM_LBUTTONDOWN:
                    down = True
                if message == WM_LBUTTONUP:
                    # Reject oversleep; never complete a long-abandoned gesture.
                    if down_at is None or not self.settle <= self.clock() - down_at <= 0.5:
                        raise SocialError("posted_sequence_settle_expired")
                    up = True
                if self.post(window, thread, message, wparam, point) is not True:
                    raise SocialError("posted_sequence_post_unknown")
                if message == WM_LBUTTONDOWN:
                    down_at = self.clock()
                    self.sleep(self.settle)
            return ClickResult("REQUESTED", "posted_sequence_enqueued_not_delivery", True, True, False, False,
                               move_attempted=True, message_mode="queued")
        except BaseException as exc:
            if down:
                cancelled = True
                try:
                    self.record("CLICK_CANCEL_INTENT")
                except BaseException:
                    pass
                try:
                    enqueued = self.cancel(self.binding.account.window, thread) is True
                except BaseException:
                    pass
            if not isinstance(exc, Exception):
                raise
            return ClickResult("UNKNOWN" if move else "NOT_STARTED",
                exc.code if isinstance(exc, SocialError) else "posted_sequence_io_failed",
                down, up, cancelled, False, move_attempted=move, message_mode="queued", cancel_enqueued=enqueued)
