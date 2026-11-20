"""Bound standalone-window capabilities; no service registration or auto-discovery."""

import base64
import ctypes as C
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from wechat_agent.bridges.wechat.background_contract import (
    DEFERRED_MARKER,
    SOURCE_MARKER,
    VALUE_MARKER,
)
from wechat_agent.bridges.wechat.click_geometry import geometry_report, parse_geometry
from wechat_agent.bridges.wechat.directed_native import CLASS_ENTRY_RVA
from wechat_agent.bridges.wechat.qt_connections import inspect_clicked_connection
from wechat_agent.bridges.wechat.send_process import KillJob, unique_object
from wechat_agent.bridges.wechat.sender import InputObservation
from wechat_agent.bridges.wechat.standalone_identity import verify_standalone_filehelper
from wechat_agent.social.models import SocialError

if __package__:
    from .wechat_background_native import BackgroundNativeCapabilities
else:
    from wechat_background_native import BackgroundNativeCapabilities


class StandalonePipe:
    def __init__(self, window, *, allow_fixed_input=False, expected_marker=VALUE_MARKER, surface_script=None, root_sha256=None):
        if expected_marker not in {VALUE_MARKER, DEFERRED_MARKER, SOURCE_MARKER}:
            raise SocialError("standalone_value_marker_denied")
        self.window = window
        self.job = None
        self.input_used = False
        self.allow_fixed_input = allow_fixed_input
        self.expected_marker = expected_marker
        self.root_match_field = "root_matches_target" if root_sha256 else "root_matches_filehelper"
        self.process = subprocess.Popen([
            str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile", "-NonInteractive", "-File", str(surface_script or Path(__file__).with_name("wechat_standalone_surface.ps1")),
            "-TargetPid", str(window.pid), "-TargetHwnd", str(window.hwnd), "-Serve",
            "-ValueMarkerKind", {VALUE_MARKER: "value", DEFERRED_MARKER: "deferred", SOURCE_MARKER: "source"}[expected_marker],
            *(("-ExpectedRootSha256", root_sha256) if root_sha256 else ()),
            *(("-AllowFixedInput",) if allow_fixed_input else ())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.job = KillJob(self.process.pid)
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=3)
            raise

    def snapshot(self):
        self.process.stdin.write(b"snapshot\n")
        self.process.stdin.flush()
        raw = self.process.stdout.readline(16385)
        if len(raw) > 16384 or not raw.endswith(b"\n"):
            raise SocialError("standalone_surface_response_invalid")
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
        if (type(data) is not dict or set(data) != {"runtime_ids", "input_token", "comparison", "geometry",
                "root_class", self.root_match_field, "qt_has_keyboard_focus"}
                or data["root_class"] != "mmui::ChatSingleWindow" or data[self.root_match_field] is not True
                or type(data["qt_has_keyboard_focus"]) is not bool):
            raise SocialError("standalone_surface_shape_invalid")
        return data

    def close(self):
        self.job.terminate()
        self.process.wait(timeout=3)
        active = self.job.active()
        self.job.close()
        if active:
            raise SocialError("standalone_surface_not_reaped")

    def set_value_once(self, token, text):
        if not self.allow_fixed_input or self.input_used or text != self.expected_marker:
            raise SocialError("standalone_value_input_denied")
        self.input_used = True
        request = dict(command="fixed_value_once", token=token,
                       text_b64=base64.b64encode(text.encode()).decode("ascii"))
        self.process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        self.process.stdin.flush()
        raw = self.process.stdout.readline(16385)
        if len(raw) > 16384 or not raw.endswith(b"\n"):
            raise SocialError("standalone_value_response_unknown")
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object) == {"accepted": True}


class StandaloneCapabilities(BackgroundNativeCapabilities):
    def __init__(self, memory, window, binding, account_reader, fingerprint, observer, class_code, pipe, *, value_mode=False,
                 return_mode=False, require_cover=False):
        surface = SimpleNamespace(memory=memory, window=window, binding=binding,
                                  account_reader=account_reader, fingerprint=fingerprint)
        super().__init__(surface, observer, class_code)
        self.pipe, self.last_snapshot = pipe, None
        self.value_mode = value_mode
        self.return_mode = return_mode
        self.require_cover = require_cover
        self.cover_points = None
        self.initial_qt_focus = None
        self.pseudo_focus_owned = False

    def _native(self):
        # Qt may mutate its accessible cache while completing a send. Retry
        # only a fresh read, never an input or submission, and never an actual
        # identity mismatch. The outer native/foreground/deadline guards stay.
        for attempt in range(3):
            self.observer.check()
            try:
                return verify_standalone_filehelper(self.surface.memory, self.ids["chat_input"],
                    account_reader=self.surface.account_reader, expected_fingerprint=self.surface.fingerprint)
            except SocialError as exc:
                if exc.code != "native_object_changed" or attempt == 2:
                    raise
                self.observer.check()
                time.sleep(0.01)

    def _cover(self):
        if not self.require_cover or self.cover_points is None:
            return
        user = self.user
        user.GetForegroundWindow.argtypes, user.GetForegroundWindow.restype = [], C.c_void_p
        user.WindowFromPoint.argtypes, user.WindowFromPoint.restype = [wintypes.POINT], C.c_void_p
        user.GetAncestor.argtypes, user.GetAncestor.restype = [C.c_void_p, C.c_uint], C.c_void_p
        foreground = user.GetForegroundWindow()
        owner = C.c_uint32()
        if (not foreground or foreground == self.window.hwnd
                or not user.GetWindowThreadProcessId(foreground, C.byref(owner))
                or owner.value in (0, self.window.pid)):
            raise SocialError("background_other_app_cover_required")
        previous = user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not previous:
            raise SocialError("background_cover_dpi_denied")
        try:
            for x, y in self.cover_points:
                hit = user.WindowFromPoint(wintypes.POINT(x, y))
                root = user.GetAncestor(hit, 2) if hit else None
                hit_owner = C.c_uint32()
                if (not root or not user.GetWindowThreadProcessId(root, C.byref(hit_owner))
                        or hit_owner.value in (0, self.window.pid)):
                    raise SocialError("background_other_app_cover_required")
            current = user.GetForegroundWindow()
            current_owner = C.c_uint32()
            if (not current or not user.GetWindowThreadProcessId(current, C.byref(current_owner))
                    or current_owner.value in (0, self.window.pid)):
                raise SocialError("background_other_app_cover_required")
        finally:
            if not user.SetThreadDpiAwarenessContext(previous):
                raise SocialError("background_cover_dpi_restore_failed")

    def observe(self):
        data = self.pipe.snapshot()
        if self.ids is None:
            self.ids, self.token = data["runtime_ids"], data["input_token"]
        if self.ids != data["runtime_ids"] or self.token != data["input_token"]:
            raise SocialError("standalone_editor_changed")
        self._native()
        comparison = data["comparison"]
        if (comparison["text_status"] != "read" or comparison["value_sha256"] != comparison["text_sha256"]
                or comparison["value_empty"] != comparison["text_empty"] or not comparison["exact_equal"]):
            raise SocialError("standalone_draft_patterns_disagree")
        observation = self.observation_type(self.surface.binding.account.account_id, self.observed_username(), self.window,
            "native_username", self.token, False, comparison["value_empty"], comparison["value_sha256"])
        snapshot = parse_geometry(data["geometry"], observation)
        checks = geometry_report(snapshot)["checks"]
        required = ("button_size_bounded", "button_inside_client", "center_has_margin",
                    "client_mapping_consistent", "client_point_packable", "button_visible", "no_capture")
        for key in required:
            if checks[key] is not True:
                raise SocialError("standalone_geometry_" + key + "_denied")
        if self.require_cover:
            rect, center = snapshot.client_bounds, snapshot.button_bounds.center
            self.cover_points = ((rect.left + 2, rect.top + 2), (rect.right - 3, rect.top + 2),
                                 (rect.left + 2, rect.bottom - 3), (rect.right - 3, rect.bottom - 3),
                                 (center.x, center.y))
            self._cover()
        connection = inspect_clicked_connection(self.surface.memory, self.ids["button"], self.ids["chat_input"])
        if (not connection["stable"] or connection["connections"] != 1
                or not connection["same_input_view"] or connection["pressed_bit_set"]):
            raise SocialError("standalone_button_not_released")
        self.last_snapshot = data
        if self.initial_qt_focus is None:
            self.initial_qt_focus = data["qt_has_keyboard_focus"]
        center = snapshot.button_bounds.center
        return observation, snapshot.enabled, (center.x, center.y)

    observation_type = InputObservation

    def observed_username(self):
        return "filehelper"

    def guard(self):
        self.observer.check()
        memory, window = self.surface.memory, self.window
        memory.validate()
        if self.user.IsIconic(window.hwnd):
            raise SocialError("standalone_window_minimized")
        owner = C.c_uint32()
        thread = self.user.GetWindowThreadProcessId(window.hwnd, C.byref(owner))
        entry = self.user.GetClassLongPtrW(window.hwnd, -24)
        if (not thread or owner.value != window.pid or self.thread is not None and self.thread != thread
                or not self.user.IsWindowVisible(window.hwnd) or self.user.IsIconic(window.hwnd)
                or entry != memory.base + CLASS_ENTRY_RVA or len(self.expected_code) != 128
                or memory.read_bytes(entry, 128) != self.expected_code):
            raise SocialError("standalone_native_receiver_changed")
        self.thread = thread
        if self.ids is not None:
            self._native()
        memory.validate()
        if self.user.GetClassLongPtrW(window.hwnd, -24) != entry:
            raise SocialError("standalone_class_changed")
        self._cover()
        self.observer.check()
        return True

    def verify_cleanup(self):
        expected_focus = False if self.pseudo_focus_owned else self.initial_qt_focus
        for attempt in range(3):
            self.guard()
            self.observe()
            if self.last_snapshot["qt_has_keyboard_focus"] is expected_focus:
                return self.guard()
            if attempt < 2:
                time.sleep(0.01)  # Read-only settling; never post cleanup twice.
        return False

    def set_value_once(self, token, text):
        if not self.value_mode or token != self.token or text != self.pipe.expected_marker:
            raise SocialError("standalone_value_input_denied")
        self.observer.set_phase("value_input")
        self.guard()
        return self.pipe.set_value_once(token, text)

    def post(self, message, wparam, lparam):
        if self.pipe.expected_marker == SOURCE_MARKER:
            raise SocialError("background_source_effect_denied")
        if self.return_mode and message in {0x200, 0x201, 0x202}:
            raise SocialError("background_return_mouse_denied")
        phases = {7: "pseudo_focus_start", 8: "pseudo_focus_end", 0x200: "mouse_move",
                  0x201: "mouse_down", 0x202: "mouse_up", 0x102: "char_input",
                  0x100: "return_down", 0x101: "return_up"}
        if message in phases:
            self.observer.set_phase(phases[message])
        if message == 7:
            self.pseudo_focus_owned = True
        return super().post(message, wparam, lparam)
