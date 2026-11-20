"""Offline-first adapter of whale_gril's WeChatSender.click_screen sequence.

Source: Banan1cee/whale_gril, commit 0af4dcd5596192e0fa4d3938ea93b404771d6e02,
bridge/bg_sender.py:421-446. Copyright (c) 2026 鲸鲸 / whale-girl contributors.
MIT license: assets/licenses/whale_gril-MIT.txt (including attribution notes).

Preserves MOVE -> DOWN -> settle -> UP. Adds owner commits, fresh native
observations and bounded press-state polling. IO is injected; this module
does not load Win32, register a live backend, type, navigate, or clear drafts.
The post/cancel callbacks must independently validate the ORIGINAL bound
HWND/process generation/thread immediately before PostMessageW.
"""

import math
import time
from dataclasses import dataclass

from ...social.models import SocialError
from .directed_click import (
    MK_LBUTTON,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    ClickResult,
    ClickSnapshot,
    pack_client_point,
)
from .sender import InputObservation


@dataclass(frozen=True)
class PostedObservation:
    snapshot: ClickSnapshot
    pressed: bool

    def __post_init__(self):
        if not isinstance(self.snapshot, ClickSnapshot) or type(self.pressed) is not bool:
            raise SocialError("posted_observation_invalid")


class PostedClick:
    """Single queued gesture, never delivery confirmation or queue-drain proof.

    Posting is not processing. A MOVE-only interruption is UNKNOWN, too.
    The minimum DOWN/UP gap comes from upstream; a current pressed observation
    is ALSO required before UP. After any DOWN attempt, cancellation is best
    effort to the original receiver, never a replacement UP or another click.
    """

    def __init__(self, binding, action, initial, *, observe, post, cancel, record,
                 settle=0.07, press_timeout=0.5, poll_interval=0.02,
                 clock=time.monotonic, sleep=time.sleep):
        for value, lower, upper in ((settle, 0.07, 0.25), (press_timeout, settle, 1.0), (poll_interval, 0.01, 0.1)):
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("queued observation timing must be bounded")
        if not isinstance(initial, InputObservation):
            raise SocialError("click_initial_input_invalid")
        initial.verify(binding, action)
        self.binding, self.action, self.initial = binding, action, initial
        self.observe, self.post, self.cancel, self.record = observe, post, cancel, record
        self.settle, self.press_timeout, self.poll_interval = settle, press_timeout, poll_interval
        self.clock, self.sleep, self.used = clock, sleep, False

    def _checked(self, *, previous=None, after_down=False):
        value = self.observe()
        if not isinstance(value, PostedObservation):
            raise SocialError("posted_observation_invalid")
        value.snapshot.verify(self.binding, self.action, self.initial, previous=previous, after_down=after_down)
        if not after_down and value.pressed:
            raise SocialError("posted_button_already_pressed")
        return value

    def _wait_pressed(self, before):
        deadline = self.clock() + self.press_timeout
        self.sleep(self.settle)  # Upstream clickSettle; never replace with an immediate UP.
        for _ in range(32):  # Bound observer calls even if the supplied clock stalls.
            if self.clock() > deadline:
                raise SocialError("posted_press_wait_deadline_before_sample")
            current = self._checked(previous=before, after_down=True)
            if self.clock() > deadline:
                # A late read (even True) cannot establish a timely pressed state.
                # Keep this distinct from timely samples that all read False.
                raise SocialError("posted_press_observation_overran_deadline")
            if current.pressed:
                return
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise SocialError("posted_button_press_not_observed")
            self.sleep(min(self.poll_interval, remaining))
        raise SocialError("posted_press_poll_limit_reached")

    def perform_once(self):
        if self.used:
            raise SocialError("posted_click_already_used")
        self.used = True
        move = down = up = cancel_attempted = cancel_enqueued = False
        receiver_thread = None
        try:
            self.record("CLICK_BEGIN_INTENT")
            before = self._checked().snapshot
            receiver_thread = before.receiver_thread
            point = pack_client_point(before.client_point)
            window = self.binding.account.window
            for stage, message, wparam in (("CLICK_MOVE_INTENT", WM_MOUSEMOVE, 0), ("CLICK_DOWN_INTENT", WM_LBUTTONDOWN, MK_LBUTTON)):
                self.record(stage)
                # A committed intent may survive a killed child even before its call.
                move = True
                down = message == WM_LBUTTONDOWN
                self._checked(previous=before)  # Recheck after the parent's commit/ACK.
                if self.post(window, receiver_thread, message, wparam, point) is not True:
                    raise SocialError("posted_move_unknown" if message == WM_MOUSEMOVE else "posted_down_unknown")
            self._wait_pressed(before)
            self.record("CLICK_UP_INTENT")
            up = True
            if not self._checked(previous=before, after_down=True).pressed:
                raise SocialError("posted_button_press_lost_before_up")
            if self.post(window, receiver_thread, WM_LBUTTONUP, 0, point) is not True:
                raise SocialError("posted_up_unknown")
            return ClickResult("REQUESTED", "window_messages_enqueued_not_delivery", True, True, False, False,
                               move_attempted=True, message_mode="queued")
        except BaseException as exc:
            reason = exc.code if isinstance(exc, SocialError) else "posted_click_rejected_or_io_failed"
            if down:
                try:
                    self.record("CLICK_CANCEL_INTENT")
                except BaseException:
                    pass  # Preserve the existing best-effort cleanup contract.
                cancel_attempted = True
                try:
                    cancel_enqueued = self.cancel(self.binding.account.window, receiver_thread) is True
                except BaseException:
                    pass
            if not isinstance(exc, Exception):
                raise
            return ClickResult("UNKNOWN" if move else "NOT_STARTED", reason, down, up, cancel_attempted, False,
                               move_attempted=move, message_mode="queued", cancel_enqueued=cancel_enqueued)
