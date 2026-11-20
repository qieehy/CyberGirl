"""Dependency-injected B18 control, with no default service registration.

The pinned AGPL-3.0-only Whale Girl source stays in research with its LICENSE
and NOTICE. Definitions are loaded in memory; this is a constrained upstream
control, not a claim that unchanged upstream or WeChat delivery works.

The host must own the original Ledger permits, killable child, write locks,
accessibility lease, native target guard and independent echo verification.
The isolated trial host requires a concrete, single-use operator review.
"""

import ast
import builtins
import hashlib
import math
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace

from wechat_agent.bridges.wechat.background_contract import (
    BMP_MARKER,
    CONTROL_MARKER,
    COVERED_MARKER,
    DEFERRED_MARKER,
    INTEGRATED_MARKER,
    RESTART_MARKER,
    RETURN_MARKER,
    SOURCE_MARKER,
    VALUE_MARKER,
)
from wechat_agent.bridges.wechat.background_text import require_background_bmp_text
from wechat_agent.bridges.wechat.sender import InputObservation
from wechat_agent.social.models import SocialError

if __package__:
    from .audit_wechat_background import FUNCTIONS, verified_tree
else:
    from audit_wechat_background import FUNCTIONS, verified_tree

WM_SETFOCUS, WM_KILLFOCUS, WM_CHAR = 7, 8, 0x102
WM_MOVE, WM_DOWN, WM_UP = 0x200, 0x201, 0x202
WM_KEYDOWN, WM_KEYUP, VK_RETURN = 0x100, 0x101, 0x0D
MARKER = CONTROL_MARKER


@dataclass(frozen=True)
class ControlResult:
    state: str
    reason: str
    input_possible: bool
    submit_possible: bool
    focus_cleanup_verified: bool
    input_units_attempted: int
    posts_attempted: int
    upstream_ok: bool | None
    # UI completion never asserts delivery, including upstream's sent result.
    delivery_verified: bool = False


class BackgroundControl:
    """One in-memory invocation; every effect has a bounded, validated order.

    observe returns (InputObservation, send_enabled, physical_send_xy).
    guard checks original process/thread/class/account/canonical page/editor
    and foreground observation continuity, without a UIA/parent roundtrip in
    the mouse gesture. transport exposes only pinned-window message posting
    and coordinate conversion, never global keyboard, mouse or focus methods.
    record must durably commit before each effect; False is a rejection.
    """

    trial_marker = CONTROL_MARKER
    input_method = "utf16_messages"
    deferred_focus = False
    submit_method = "window_mouse"

    def __init__(self, binding, action, *, observe, guard, transport, record,
                 clock=time.perf_counter, sleep=time.sleep, timeout=20):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 1 <= timeout <= 20):
            raise ValueError("control deadline must be bounded")
        if action.text != self.trial_marker:
            raise SocialError("background_control_marker_denied")
        self.binding, self.action = binding, action
        self.observe, self.guard, self.transport, self.record = observe, guard, transport, record
        self.clock, self.sleep, self.timeout = clock, sleep, timeout
        self.used = self.focus = self.cleaned = self.move = self.down = self.up = False
        self.input_count = self.posts = 0
        self.initial = self.xy = self.deadline = None
        self.upstream_ok = None
        self.fault = None
        self.cleanup_posted = False
        self.value_used = False
        self.return_down = self.return_up = self.return_used = False
        self.units = [int.from_bytes(action.text.encode("utf-16-le")[i:i + 2], "little")
                      for i in range(0, len(action.text.encode("utf-16-le")), 2)]
        if len(self.units) > 96:
            raise SocialError("background_control_text_bound")

    def _check(self):
        if self.clock() >= self.deadline:
            raise SocialError("background_control_deadline")
        if self.guard() is not True:
            raise SocialError("background_control_binding_lost")
        if self.clock() >= self.deadline:
            raise SocialError("background_control_deadline")

    def _read(self, hwnd, **_kwargs):
        if hwnd != self.binding.account.window.hwnd:
            raise SocialError("background_control_window_changed")
        self._check()
        observation, enabled, xy = self.observe()
        if not isinstance(observation, InputObservation) or type(enabled) is not bool:
            raise SocialError("background_control_observation_invalid")
        if self.initial is None:
            observation.verify(self.binding, self.action, require_focus=False)
            self.initial = observation
        else:
            # Preserve identity even while text is partial or after submission.
            if (observation.account_id, observation.username, observation.window,
                observation.identity_method, observation.input_token) != (
                    self.initial.account_id, self.initial.username, self.initial.window,
                    self.initial.identity_method, self.initial.input_token):
                raise SocialError("background_control_editor_changed")
        if (type(xy) is not tuple or len(xy) != 2 or any(type(v) is not int for v in xy)
                or any(not -32768 <= v <= 32767 for v in xy)):
            raise SocialError("background_control_geometry_invalid")
        self._check()
        if self.xy is None:
            self.xy = xy
        elif self.xy != xy:
            raise SocialError("background_control_geometry_changed")
        # Expose only the marker, empty, or a mismatch sentinel. Never send UI
        # text/drafts into the upstream logger, report or persistent journal.
        empty_hash = hashlib.sha256(b"").hexdigest()
        marker_hash = hashlib.sha256(self.action.text.encode()).hexdigest()
        value = ("" if observation.empty and observation.draft_sha256 == empty_hash
                 else self.action.text if not observation.empty and observation.draft_sha256 == marker_hash
                 else "<draft-mismatch>")
        return {"nodes": [None] * 20, "edit": self.initial.input_token,
                "value": value, "send_xy": xy if enabled or not self.input_count else None,
                "chat": "filehelper"}

    def _post(self, message, wparam, lparam):
        try:
            return self._post_checked(message, wparam, lparam)
        except BaseException as exc:
            if self.fault is None:
                self.fault = exc.code if isinstance(exc, SocialError) else "background_control_post_failed"
            raise

    def _post_checked(self, message, wparam, lparam):
        if self.fault is not None and message != WM_KILLFOCUS:
            raise SocialError("background_control_prior_failure")
        self._check()
        if message == WM_SETFOCUS:
            expected_units = len(self.units) if self.deferred_focus else 0
            if self.focus or self.input_count != expected_units or (wparam, lparam) != (0, 0):
                raise SocialError("background_control_focus_order")
            # A fresh empty-draft check follows the durable input intent.
            current = self._read(self.binding.account.window.hwnd)
            if current["value"] != (self.action.text if self.deferred_focus else ""):
                raise SocialError("background_control_draft_changed")
            stage = "PSEUDO_FOCUS_INTENT"
            self.focus = True
        elif message == WM_CHAR:
            if (not self.focus or self.cleaned or self.move or self.input_count >= len(self.units)
                    or (wparam, lparam) != (self.units[self.input_count], 1)):
                raise SocialError("background_control_input_order")
            stage = "CHAR_INTENT"
            self.input_count += 1
        elif message in (WM_MOVE, WM_DOWN, WM_UP):
            if (self.cleaned or self.input_count != len(self.units)
                    or lparam != self.transport.expected_point(self.xy)):
                raise SocialError("background_control_submit_order")
            if message == WM_MOVE and not self.move and wparam == 0:
                ready = self._read(self.binding.account.window.hwnd)
                if ready["value"] != self.action.text or ready["send_xy"] is None:
                    raise SocialError("background_control_submit_draft_or_disabled")
                if self.deferred_focus and not self.focus:
                    self._post(WM_SETFOCUS, 0, 0)
                    self._sleep(0.1)
                stage, self.move = "MOVE_INTENT", True
            elif message == WM_DOWN and self.move and not self.down and wparam == 1:
                stage, self.down = "DOWN_INTENT", True
            elif message == WM_UP and self.down and not self.up and wparam == 0:
                stage, self.up = "UP_INTENT", True
            else:
                raise SocialError("background_control_submit_order")
        elif message == WM_KILLFOCUS:
            if not self.focus or self.cleaned or (wparam, lparam) != (0, 0):
                raise SocialError("background_control_cleanup_order")
            stage = "PSEUDO_FOCUS_END_INTENT"
            # Even an uncertain cleanup cannot be reattempted automatically.
            self.cleaned = True
        else:
            raise SocialError("background_control_message_denied")
        # All mouse intents are durable before MOVE; no journal/parent roundtrip
        # interrupts the upstream MOVE -> DOWN -> sleep -> UP gesture.
        stages = ("MOVE_INTENT", "DOWN_INTENT", "UP_INTENT") if message == WM_MOVE else (
            () if message in (WM_DOWN, WM_UP) else (stage,))
        for intent in stages:
            if self.record(intent) is not True:
                raise SocialError("background_control_intent_not_committed")
        self._check()  # Close a possible journal/IPC pause before actual posting.
        self.posts += 1
        if self.transport.post(message, wparam, lparam) is not True:
            raise SocialError("background_control_post_unknown")
        if message == WM_KILLFOCUS:
            self.cleanup_posted = True
        self._check()
        return True

    def _sleep(self, seconds):
        self._check()
        if seconds < 0 or self.clock() + seconds >= self.deadline:
            raise SocialError("background_control_deadline")
        self.sleep(seconds)
        self._check()

    def _value_input(self, text):
        if self.value_used or text != self.action.text:
            raise SocialError("background_value_input_already_used")
        self._check()
        current = self._read(self.binding.account.window.hwnd)
        if current["value"] != "":
            raise SocialError("background_value_existing_draft")
        if not self.deferred_focus:
            self._post(WM_SETFOCUS, 0, 0)
            self._sleep(0.1)
        self.value_used = True
        if self.record("VALUE_INPUT_INTENT") is not True:
            raise SocialError("background_value_intent_not_committed")
        self.input_count = len(self.units)
        self._check()
        if self.transport.set_value_once(self.initial.input_token, text) is not True:
            raise SocialError("background_value_input_unknown")
        self._check()

    def run_once(self, *, write_guard):
        if self.used:
            raise SocialError("background_control_already_used")
        self.used = True
        self.deadline = self.clock() + self.timeout
        reason, cleanup_ok, requested = "background_control_not_started", False, False
        try:
            with write_guard:
                # Verify provenance BEFORE evaluating any upstream definitions.
                tree = verified_tree()
                names = FUNCTIONS | {"encode_utf16", "WeChatSender"}
                definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                               and n.name in names]
                if {n.name for n in definitions} != names:
                    raise SocialError("background_control_source_changed")
                namespace = self._namespace()
                exec(compile(ast.Module(body=definitions, type_ignores=[]), "<constrained-upstream>", "exec"), namespace)
                sender_type = namespace["WeChatSender"]
                control = self

                class BoundSender(sender_type):
                    def _post(self, msg, wparam, lparam):
                        return control._post(msg, wparam, lparam)

                    def type_text(self, text, **kwargs):
                        if control.input_method in {"uia_value_once", "uia_deferred_focus"}:
                            control._value_input(text)
                            return True, []
                        return super().type_text(text, **kwargs)

                    def click_screen(self, sx, sy, settle=None):
                        if control.submit_method == "window_return":
                            return control._return_submit((sx, sy))
                        return super().click_screen(sx, sy, settle=settle)

                    def end_input(self):
                        if control.focus:
                            super().end_input()

                namespace["WeChatSender"] = BoundSender
                try:
                    self.upstream_ok, reason = namespace["send"](
                        self.action.text, chat=None, hwnd=self.binding.account.window.hwnd,
                        char_mode="utf16", strip_unencodable=False, verify=True, unhide=False,
                    )
                    requested = self.upstream_ok is True and (self.up or self.return_up)
                finally:
                    if self.focus and not self.cleaned:
                        BoundSender(self.binding.account.window.hwnd).end_input()
                    # Posting KILLFOCUS alone cannot verify its processing.
                    if self.focus:
                        cleanup_ok = self.cleanup_posted and self.transport.verify_cleanup() is True
                    elif self.value_used:
                        cleanup_ok = self.transport.verify_cleanup() is True
                    else:
                        cleanup_ok = True
        except Exception as exc:
            reason = exc.code if isinstance(exc, SocialError) else "background_control_failed"
            requested = False
        if self.fault is not None:
            reason, requested = self.fault, False
        touched = self.focus or self.value_used
        state = "REQUESTED" if requested and cleanup_ok else "UNKNOWN" if touched else "NOT_STARTED"
        return ControlResult(state, reason, touched, self.down or self.return_down, cleanup_ok,
                             self.input_count, self.posts, self.upstream_ok)

    def _namespace(self):
        # Only the two coordinate APIs required by unchanged click_screen are
        # exposed. No WinDLL loading, import hooks or global user32 object.
        from ctypes import byref, wintypes

        def denied(*_args, **_kwargs):
            raise SocialError("background_control_upstream_effect_denied")

        def no_import(*_args, **_kwargs):
            raise SocialError("background_control_upstream_import_denied")

        def clear_denied(*_args, **_kwargs):
            self.fault = self.fault or "background_control_body_mismatch"
            raise SocialError("background_control_body_mismatch")

        return {
            "__builtins__": {**vars(builtins), "__import__": no_import},
            "re": re, "ctypes": SimpleNamespace(byref=byref), "wintypes": wintypes,
            "DPI_AWARE": True,  # Host transport must enforce physical coordinates.
            "user32": SimpleNamespace(IsIconic=self.transport.is_iconic,
                                      ScreenToClient=self.transport.screen_to_client),
            "WM_SETFOCUS": WM_SETFOCUS, "WM_KILLFOCUS": WM_KILLFOCUS, "WM_CHAR": WM_CHAR,
            "WM_MOUSEMOVE": WM_MOVE, "WM_LBUTTONDOWN": WM_DOWN, "WM_LBUTTONUP": WM_UP,
            "MIN_NODES": 20, "_WriteGuard": nullcontext,
            "_ensure_tree": self._read, "read_ui": self._read,
            "_clear_composer": clear_denied, "detect_char_mode": denied, "open_chat": denied,
            "_CONFIGURED_MODES": {}, "_DETECTED_MODES": {}, "_MODE_LOGGED": set(),
            "_DETECT_DIRTY": [], "_remember_char_mode": lambda *_: None,
            "TIMING": {"focusGap": 0.1, "clickSettle": 0.07, "sendEnabled": 0.35,
                       "afterClick": 0.4, "verifyPoll": 0.2},
            "time": SimpleNamespace(time=self.clock, sleep=self._sleep),
            "UipiDenied": type("UipiDenied", (RuntimeError,), {}),
        }


class StandaloneValueControl(BackgroundControl):
    """Explicit new experiment: one complete bound UIA value, no WM_CHAR input."""

    trial_marker = VALUE_MARKER
    input_method = "uia_value_once"


class StandaloneBmpControl(BackgroundControl):
    """Explicit printable-BMP scope; unchanged upstream window-message input."""

    trial_marker = BMP_MARKER
    input_method = "utf16_bmp_messages"

    def __init__(self, binding, action, **kwargs):
        require_background_bmp_text(action.text)
        super().__init__(binding, action, **kwargs)


class StandaloneReturnControl(StandaloneBmpControl):
    """Only submit changes: one bound-window Return down/up, no mouse path."""

    trial_marker = RETURN_MARKER
    input_method = "utf16_bmp_return"
    submit_method = "window_return"

    def _return_submit(self, xy):
        try:
            return self._return_submit_checked(xy)
        except BaseException as exc:
            if self.fault is None:
                self.fault = exc.code if isinstance(exc, SocialError) else "background_return_failed"
            raise

    def _return_submit_checked(self, xy):
        self._check()
        if self.return_used or not self.focus or self.input_count != len(self.units) or xy != self.xy:
            raise SocialError("background_return_order_denied")
        self.return_used = True
        current = self._read(self.binding.account.window.hwnd)
        if current["value"] != self.action.text or current["send_xy"] is None:
            raise SocialError("background_control_body_mismatch")
        # Both intents precede either enqueue. No parent/UIA roundtrip can
        # split the key gesture; every enqueue still has the native guard.
        for stage in ("RETURN_DOWN_INTENT", "RETURN_UP_INTENT"):
            if self.record(stage) is not True:
                raise SocialError("background_return_intent_not_committed")
        for message, lparam, flag in ((WM_KEYDOWN, 0x001C0001, "return_down"),
                                      (WM_KEYUP, 0xC01C0001, "return_up")):
            self._check()
            setattr(self, flag, True)
            self.posts += 1
            if self.transport.post(message, VK_RETURN, lparam) is not True:
                raise SocialError("background_return_post_unknown")
            self._check()
        return True


class StandaloneCoveredControl(StandaloneReturnControl):
    """Same Return route with independently enforced active-app occlusion."""

    trial_marker = COVERED_MARKER
    input_method = "utf16_bmp_covered"


class StandaloneDeferredControl(StandaloneValueControl):
    """Only change: verify full value before creating any pseudo-window focus."""

    trial_marker = DEFERRED_MARKER
    input_method = "uia_deferred_focus"
    deferred_focus = True


class StandaloneSourceDiagnostic(StandaloneDeferredControl):
    """One fixed value solely for observation. No upstream send or messages."""

    trial_marker = SOURCE_MARKER
    input_method = "uia_source_diagnostic"

    def _post(self, *_args):
        raise SocialError("background_source_effect_denied")

    def run_once(self, *, write_guard):
        if self.used:
            raise SocialError("background_control_already_used")
        self.used = True
        self.deadline = self.clock() + self.timeout
        reason, cleanup_ok = "background_source_not_started", False
        try:
            with write_guard:
                self._value_input(self.action.text)
                current = self._read(self.binding.account.window.hwnd)
                if current["value"] != self.action.text:
                    raise SocialError("background_control_body_mismatch")
                cleanup_ok = self.transport.verify_cleanup() is True
                self._check()
                reason = "background_source_diagnostic_complete"
        except Exception as exc:
            reason = exc.code if isinstance(exc, SocialError) else "background_source_diagnostic_failed"
        # This purposefully leaves an operator-reviewed draft; never delivery.
        return ControlResult("UNKNOWN" if self.value_used else "NOT_STARTED", reason,
                             self.value_used, False, cleanup_ok, self.input_count, 0, None)


class StandaloneIntegrationControl(StandaloneCoveredControl):
    """Only the two separately reviewed service acceptance markers."""
    input_method = "utf16_bmp_integrated"

    def __init__(self, binding, action, **kwargs):
        if action.text not in {INTEGRATED_MARKER, RESTART_MARKER}:
            raise SocialError("background_integrated_marker_denied")
        self.trial_marker = action.text
        super().__init__(binding, action, **kwargs)


class WorkbenchControl(StandaloneCoveredControl):
    """Final body from an owned parent pipe; parent persists per-stage permits."""

    input_method = "workbench_background_bmp"

    def __init__(self, binding, action, **kwargs):
        require_background_bmp_text(action.text)
        self.trial_marker = action.text
        super().__init__(binding, action, **kwargs)
