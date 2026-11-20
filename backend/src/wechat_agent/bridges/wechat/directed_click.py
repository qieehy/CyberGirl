"""Offline candidate for a single HWND-directed mouse gesture.

There is no production observer or emitter here and no sender registration.
Real WeChat routing/cancellation behavior has not been validated. Tests inject
observations and use only an application-owned hidden receiver window.
"""

import math
import re
from dataclasses import dataclass

from ...social.models import SocialError
from .sender import InputObservation

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_CANCELMODE = 0x001F
MK_LBUTTON = 1


@dataclass(frozen=True)
class Point:
    x: int
    y: int

    def __post_init__(self):
        if any(type(v) is not int or not -(2**30) <= v < 2**30 for v in (self.x, self.y)):
            raise SocialError("click_point_invalid")


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        values = (self.left, self.top, self.right, self.bottom)
        if (any(type(v) not in (int, float) or not math.isfinite(v) or not -(2**30) <= v < 2**30 for v in values)
                or self.right <= self.left or self.bottom <= self.top):
            raise SocialError("click_rectangle_invalid")

    @property
    def center(self):
        return Point(math.floor((self.left + self.right) / 2), math.floor((self.top + self.bottom) / 2))

    def contains(self, point, *, margin=0):
        return (self.left + margin <= point.x < self.right - margin
                and self.top + margin <= point.y < self.bottom - margin)


@dataclass(frozen=True)
class ClickSnapshot:
    input: InputObservation
    button_token: str
    button_bounds: Rect  # UIA physical screen pixels, may have a negative origin.
    client_bounds: Rect  # Client rectangle mapped to physical screen pixels.
    client_point: Point  # ScreenToClient result for button_bounds.center.
    dpi: int
    coordinate_space: str
    receiver_hwnd: int
    receiver_pid: int
    receiver_thread: int
    foreground_hwnd: int
    hit_root_hwnd: int
    hit_pid: int
    hit_button_token: str
    capture_hwnd: int
    focus_kind: str  # chat_input, send_button, or other; proven by the observer.
    enabled: bool
    visible: bool
    input_busy: bool  # Hardware mouse buttons/modifiers currently pressed.

    def verify(self, binding, action, initial, *, after_down=False, previous=None):
        if not isinstance(self.input, InputObservation):
            raise SocialError("click_input_observation_invalid")
        self.input.verify(binding, action, initial=initial, require_focus=not after_down)
        window = binding.account.window
        if (any(type(v) is not int or v <= 0 for v in (self.receiver_hwnd, self.receiver_pid, self.receiver_thread,
                                                       self.foreground_hwnd, self.hit_root_hwnd, self.hit_pid))
                or self.receiver_hwnd != window.hwnd or self.receiver_pid != window.pid
                or type(self.receiver_thread) is not int or self.receiver_thread <= 0
                or self.foreground_hwnd != window.hwnd or self.hit_root_hwnd != window.hwnd
                or self.hit_pid != window.pid or self.hit_button_token != self.button_token):
            raise SocialError("click_window_or_hit_target_changed")
        if (not isinstance(self.button_token, str) or not re.fullmatch(r"[0-9a-f]{64}", self.button_token)
                or any(type(v) is not bool for v in (self.enabled, self.visible, self.input_busy))
                or not self.enabled or not self.visible or self.input_busy):
            raise SocialError("click_button_not_ready")
        if ((self.input.focused and self.focus_kind != "chat_input")
                or (not self.input.focused and (not after_down or self.focus_kind != "send_button"))):
            raise SocialError("click_focus_not_owned")
        allowed_capture = {0, window.hwnd} if after_down else {0}
        if type(self.capture_hwnd) is not int or self.capture_hwnd not in allowed_capture:
            raise SocialError("click_capture_not_owned")
        if (self.coordinate_space != "physical_per_monitor_v2" or type(self.dpi) is not int or not 48 <= self.dpi <= 768
                or not isinstance(self.button_bounds, Rect) or not isinstance(self.client_bounds, Rect)
                or not isinstance(self.client_point, Point)):
            raise SocialError("click_coordinate_space_unverified")
        button, client = self.button_bounds, self.client_bounds
        if (not 8 <= button.right - button.left <= 512 or not 8 <= button.bottom - button.top <= 256
                or any(type(v) is not int for v in (client.left, client.top, client.right, client.bottom))
                or button.left < client.left or button.right > client.right
                or button.top < client.top or button.bottom > client.bottom or not button.contains(button.center, margin=2)
                or self.client_point.x != button.center.x - client.left
                or self.client_point.y != button.center.y - client.top):
            raise SocialError("click_geometry_unverified")
        pack_client_point(self.client_point)
        if previous is not None and any(getattr(self, key) != getattr(previous, key) for key in (
                "button_token", "button_bounds", "client_bounds", "client_point", "dpi", "coordinate_space", "receiver_thread")):
            raise SocialError("click_geometry_or_control_changed")


def pack_client_point(point):
    # Explicit nonnegative client bounds avoid overflow/sign errors in LPARAM.
    if not isinstance(point, Point) or not 0 <= point.x <= 32767 or not 0 <= point.y <= 32767:
        raise SocialError("click_client_point_out_of_range")
    return point.x | (point.y << 16)


@dataclass(frozen=True)
class ClickResult:
    state: str  # NOT_STARTED, REQUESTED, UNKNOWN; never a delivery confirmation.
    reason: str
    down_attempted: bool
    up_attempted: bool
    cancel_attempted: bool
    cancel_acknowledged: bool
    move_attempted: bool = False
    message_mode: str = "synchronous"
    cancel_enqueued: bool = False

    def __post_init__(self):
        if (type(self.state) is not str or self.state not in {"NOT_STARTED", "REQUESTED", "UNKNOWN"}
                or not isinstance(self.reason, str) or not re.fullmatch(r"[a-z_0-9]{1,128}", self.reason)
                or any(type(v) is not bool for v in (self.down_attempted, self.up_attempted,
                        self.cancel_attempted, self.cancel_acknowledged, self.move_attempted, self.cancel_enqueued))
                or type(self.message_mode) is not str or self.message_mode not in {"synchronous", "queued", "injected"}
                or self.message_mode == "injected" and (self.move_attempted or self.cancel_acknowledged or self.cancel_enqueued)
                or self.message_mode == "synchronous" and (self.move_attempted or self.cancel_enqueued)
                or self.message_mode == "queued" and (self.cancel_acknowledged
                    or self.down_attempted and not self.move_attempted)
                or self.up_attempted and not self.down_attempted
                or self.cancel_attempted and not self.down_attempted
                or self.cancel_acknowledged and not self.cancel_attempted
                or self.cancel_enqueued and not self.cancel_attempted
                or self.state == "NOT_STARTED" and (self.move_attempted or self.down_attempted)
                or self.state == "UNKNOWN" and not (self.move_attempted or self.down_attempted)
                or self.state == "REQUESTED" and (not self.down_attempted or not self.up_attempted
                                                   or self.cancel_attempted)):
            raise SocialError("invalid_gesture_result")


class DirectedClick:
    """One use after an external durable submit permit, with injected IO only.

    Timeout is not proof of non-execution. After any down attempt, a failure
    never sends up to a newly observed target and never retries the gesture.
    A cancellation acknowledgement is not proof that a remote app cancelled.
    """

    def __init__(self, binding, action, initial, *, observe, emit, cancel, record, message_timeout_ms=150):
        if type(message_timeout_ms) is not int or not 1 <= message_timeout_ms <= 250:
            raise ValueError("each directed message needs a nonzero bounded timeout")
        if not isinstance(initial, InputObservation):
            raise SocialError("click_initial_input_invalid")
        initial.verify(binding, action)
        self.binding, self.action, self.initial = binding, action, initial
        self.observe, self.emit, self.cancel, self.record = observe, emit, cancel, record
        self.timeout = message_timeout_ms
        self.used = False

    def perform_once(self):
        if self.used:
            raise SocialError("directed_click_already_used")
        self.used = True
        down = up = cancel_attempted = cancel_ack = False
        receiver_thread = None
        try:
            self.record("CLICK_BEGIN_INTENT")  # Consume use before observing the gesture target.
            before = self.observe()
            if not isinstance(before, ClickSnapshot):
                raise SocialError("click_snapshot_invalid")
            before.verify(self.binding, self.action, self.initial)
            receiver_thread = before.receiver_thread
            point = pack_client_point(before.client_point)
            self.record("CLICK_DOWN_INTENT")  # Must durably succeed before the call.
            down = True  # A failed/timeout call may still have reached the receiver.
            if self.emit(self.binding.account.window, receiver_thread, WM_LBUTTONDOWN, MK_LBUTTON, point, self.timeout) is not True:
                raise SocialError("click_down_unknown")
            current = self.observe()
            if not isinstance(current, ClickSnapshot):
                raise SocialError("click_snapshot_invalid")
            current.verify(self.binding, self.action, self.initial, after_down=True, previous=before)
            self.record("CLICK_UP_INTENT")
            up = True
            if self.emit(self.binding.account.window, receiver_thread, WM_LBUTTONUP, 0, point, self.timeout) is not True:
                raise SocialError("click_up_unknown")
            return ClickResult("REQUESTED", "window_messages_acknowledged", down, up, False, False)
        except BaseException as exc:
            reason = exc.code if isinstance(exc, SocialError) else "click_rejected_or_io_failed"
            if down:
                # The supplied cancellation method must independently verify
                # the ORIGINAL HWND, generation and thread, not a new UI target.
                try:
                    self.record("CLICK_CANCEL_INTENT")
                except BaseException:
                    pass  # A failed journal must not prevent best-effort cleanup.
                cancel_attempted = True
                try:
                    cancel_ack = self.cancel(self.binding.account.window, receiver_thread, self.timeout) is True
                except BaseException:
                    pass
            if not isinstance(exc, Exception):
                raise
            return ClickResult("UNKNOWN" if down else "NOT_STARTED", reason, down, up, cancel_attempted, cancel_ack)
