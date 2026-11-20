"""Bound native capabilities for the isolated B18 constrained control.

Not registered with a service. Construction alone does not post messages;
the isolated trial host must hold the original action permits and lease.
"""

import ctypes as C
import hashlib
from ctypes import wintypes

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
from wechat_agent.bridges.wechat.click_geometry import geometry_report, parse_geometry
from wechat_agent.bridges.wechat.contracts import require_send_target
from wechat_agent.bridges.wechat.directed_native import CLASS_ENTRY_RVA
from wechat_agent.bridges.wechat.native_sender import NativeSendBackend
from wechat_agent.bridges.wechat.qt_connections import inspect_clicked_connection
from wechat_agent.bridges.wechat.qt_memory import verify_current_filehelper
from wechat_agent.social.models import SocialError


class BackgroundSurface(NativeSendBackend):
    """Existing native/UIA identity checks, without a foreground requirement."""

    def configure(self, binding, action):
        if self.binding is not None:
            raise SocialError("background_native_already_configured")
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=self.window)
        if action.text in {BMP_MARKER, RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER}:
            require_background_bmp_text(action.text)
        if (action.text not in {BMP_MARKER, RETURN_MARKER, COVERED_MARKER, INTEGRATED_MARKER, RESTART_MARKER, CONTROL_MARKER, VALUE_MARKER, DEFERRED_MARKER, SOURCE_MARKER} or binding.account.self_id != binding.account.account_id
                or hashlib.sha256(binding.account.account_id.encode()).hexdigest() != self.fingerprint):
            raise SocialError("background_native_binding_denied")
        self.binding, self.action = binding, action

    def prepare_input_focus(self):
        raise SocialError("background_real_focus_denied")

    def input_text(self, *_args):
        raise SocialError("background_uia_input_denied")

    def submit_once(self, *_args):
        raise SocialError("background_uia_submit_denied")


class WorkbenchSurface(BackgroundSurface):
    """Explicit final-body mode; native account and target checks remain mandatory."""

    def configure(self, binding, action):
        if self.binding is not None:
            raise SocialError("background_native_already_configured")
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=self.window)
        require_background_bmp_text(action.text)
        if (binding.account.self_id != binding.account.account_id
                or hashlib.sha256(binding.account.account_id.encode()).hexdigest() != self.fingerprint):
            raise SocialError("background_native_binding_denied")
        self.binding, self.action = binding, action


class BackgroundNativeCapabilities:
    def __init__(self, surface, observer, expected_class_code):
        self.surface, self.observer = surface, observer
        self.window = surface.window
        self.user = surface.memory.u
        self.expected_code = expected_class_code
        self.thread = self.ids = self.token = None
        for name, args, result in (
            ("GetClassLongPtrW", [C.c_void_p, C.c_int], C.c_size_t),
            ("GetWindowThreadProcessId", [C.c_void_p, C.POINTER(C.c_uint32)], C.c_uint32),
            ("IsWindowVisible", [C.c_void_p], C.c_int),
            ("IsIconic", [C.c_void_p], C.c_int),
            ("PostMessageW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t], C.c_int),
            ("SetThreadDpiAwarenessContext", [C.c_void_p], C.c_void_p),
            ("ScreenToClient", [C.c_void_p, C.POINTER(wintypes.POINT)], C.c_int),
        ):
            getattr(self.user, name).argtypes = args
            getattr(self.user, name).restype = result

    def observe(self):
        first = self.surface.inspect()
        ids = {k: list(v) for k, v in self.surface.runtime_ids.items()}
        if self.ids is None:
            self.ids, self.token = ids, first.input_token
        if ids != self.ids or first.input_token != self.token:
            raise SocialError("background_native_editor_changed")
        snapshot = parse_geometry(self.surface.ui.click_geometry(first.input_token), first)
        report = geometry_report(snapshot)
        for key in ("button_size_bounded", "button_inside_client", "center_has_margin",
                    "client_mapping_consistent", "client_point_packable", "button_visible", "no_capture"):
            if report["checks"][key] is not True:
                raise SocialError("background_native_geometry_denied")
        connection = inspect_clicked_connection(self.surface.memory, ids["button"], ids["chat_input"])
        if (not connection["stable"] or connection["connections"] != 1
                or not connection["same_input_view"] or connection["pressed_bit_set"]):
            raise SocialError("background_native_connection_denied")
        final = self.surface.inspect()
        if final.input_token != first.input_token or final.draft_sha256 != first.draft_sha256:
            raise SocialError("background_native_surface_changed")
        center = snapshot.button_bounds.center
        return final, snapshot.enabled, (center.x, center.y)

    def guard(self):
        self.observer.check()
        memory, window = self.surface.memory, self.window
        memory.validate()
        owner = C.c_uint32()
        thread = self.user.GetWindowThreadProcessId(window.hwnd, C.byref(owner))
        if (not thread or owner.value != window.pid or self.thread is not None and thread != self.thread
                or not self.user.IsWindowVisible(window.hwnd) or self.user.IsIconic(window.hwnd)):
            raise SocialError("background_native_receiver_changed")
        self.thread = thread
        entry = self.user.GetClassLongPtrW(window.hwnd, -24)
        if (entry != memory.base + CLASS_ENTRY_RVA or len(self.expected_code) != 128
                or memory.read_bytes(entry, 128) != self.expected_code):
            raise SocialError("background_native_class_changed")
        if self.ids is not None:
            verify_current_filehelper(memory, self.ids["selected_session"], self.ids["chat_input"],
                account_reader=self.surface.account_reader,
                expected_account_fingerprint=self.surface.fingerprint)
        memory.validate()
        final_owner = C.c_uint32()
        if (self.user.GetClassLongPtrW(window.hwnd, -24) != entry
                or self.user.GetWindowThreadProcessId(window.hwnd, C.byref(final_owner)) != thread
                or final_owner.value != window.pid):
            raise SocialError("background_native_receiver_changed")
        self.observer.check()
        return True

    def is_iconic(self, hwnd):
        if hwnd != self.window.hwnd:
            raise SocialError("background_native_window_changed")
        return bool(self.user.IsIconic(hwnd))

    def screen_to_client(self, hwnd, point):
        if hwnd != self.window.hwnd:
            raise SocialError("background_native_window_changed")
        previous = self.user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not previous:
            raise SocialError("background_native_dpi_context_denied")
        try:
            return bool(self.user.ScreenToClient(hwnd, point))
        finally:
            if not self.user.SetThreadDpiAwarenessContext(previous):
                raise SocialError("background_native_dpi_restore_failed")

    def expected_point(self, xy):
        point = wintypes.POINT(*xy)
        if not self.screen_to_client(self.window.hwnd, C.byref(point)) or not (
                0 <= point.x <= 32767 and 0 <= point.y <= 32767):
            raise SocialError("background_native_client_point_denied")
        return (point.y << 16) | point.x

    def post(self, message, wparam, lparam):
        # Private object for BackgroundControl; its order/permit checks precede
        # every call. Rebind immediately at the actual enqueue boundary too.
        if self.ids is None or self.token is None:
            raise SocialError("background_native_not_prepared")
        if type(message) is not int:
            raise SocialError("background_native_message_denied")
        if message in (0x100, 0x101):
            expected = 0x001C0001 if message == 0x100 else 0xC01C0001
            if not getattr(self, "return_mode", False) or wparam != 0x0D or lparam != expected:
                raise SocialError("background_native_return_denied")
            modifier = self.user.GetAsyncKeyState
            modifier.argtypes, modifier.restype = [C.c_int], C.c_short
            if any(modifier(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
                raise SocialError("background_return_modifier_active")
        elif type(message) is not int or message not in (7, 8, 0x102, 0x200, 0x201, 0x202):
            raise SocialError("background_native_message_denied")
        self.guard()
        return bool(self.user.PostMessageW(self.window.hwnd, message, wparam, lparam))

    def verify_cleanup(self):
        self.guard()
        state = self.surface.ui.rpc("focus_state", token=self.token)
        if (type(state) is not dict or set(state) != {"input_token", "qt_has_keyboard_focus"}
                or state["input_token"] != self.token or state["qt_has_keyboard_focus"] is not False):
            return False
        self.observe()  # Same editor, released button, no capture, stable canonical target.
        self.guard()
        return True
