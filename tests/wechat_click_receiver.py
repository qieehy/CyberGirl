"""Hidden test-owned HWND receiver. Never accepts a real application's HWND."""

import ctypes as C
import json
import os
import sys
import time
from importlib.metadata import version

from wechat_agent.bridges.wechat.directed_click import (
    WM_CANCELMODE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
)
from wechat_agent.social.models import SocialError


def libraries():
    if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError("Windows x64 fixture only")
    user, kernel = C.WinDLL("user32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
    for name, args, returns in (
        ("GetWindowThreadProcessId", [C.c_void_p, C.POINTER(C.c_uint32)], C.c_uint32),
        ("GetClassNameW", [C.c_void_p, C.c_wchar_p, C.c_int], C.c_int),
        ("SendMessageTimeoutW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t, C.c_uint, C.c_uint, C.POINTER(C.c_size_t)], C.c_ssize_t),
        ("PostMessageW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t], C.c_int),
    ):
        getattr(user, name).argtypes, getattr(user, name).restype = args, returns
    for name, args, returns in (
        ("OpenProcess", [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p),
        ("CloseHandle", [C.c_void_p], C.c_int),
        ("GetProcessTimes", [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4, C.c_int),
        ("GetCurrentProcess", [], C.c_void_p),
        ("GetModuleHandleW", [C.c_wchar_p], C.c_void_p),
    ):
        getattr(kernel, name).argtypes, getattr(kernel, name).restype = args, returns
    return user, kernel


def creation_time(kernel, process):
    values = [C.c_uint64() for _ in range(4)]
    if not kernel.GetProcessTimes(process, *(C.byref(value) for value in values)):
        raise C.WinError(C.get_last_error())
    return values[0].value


class OwnedEmitter:
    """Only the test fixture's exact class/PID/generation may receive events."""

    def __init__(self, window, thread):
        self.window, self.thread = window, thread
        self.user, self.kernel = libraries()
        self.calls = []

    def validate(self, thread):
        owner = C.c_uint32()
        actual_thread = self.user.GetWindowThreadProcessId(self.window.hwnd, C.byref(owner))
        name = C.create_unicode_buffer(128)
        length = self.user.GetClassNameW(self.window.hwnd, name, len(name))
        if (owner.value != self.window.pid or actual_thread != thread or length <= 0
                or name.value != f"WeChatClickFixture_{self.window.pid}"):
            raise SocialError("fixture_receiver_identity_changed")
        handle = self.kernel.OpenProcess(0x1000, False, self.window.pid)
        if not handle:
            raise SocialError("fixture_receiver_process_missing")
        try:
            if creation_time(self.kernel, handle) != self.window.created_filetime:
                raise SocialError("fixture_receiver_generation_changed")
        finally:
            self.kernel.CloseHandle(handle)

    def emit(self, window, thread, message, wparam, lparam, timeout):
        if window != self.window or thread != self.thread:
            raise SocialError("fixture_receiver_binding_mismatch")
        if message not in {WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE} or not 1 <= timeout <= 250:
            raise SocialError("fixture_message_denied")
        self.validate(self.thread)
        self.calls.append((message, wparam, lparam))
        value = C.c_size_t()
        C.set_last_error(0)
        # BLOCK | ABORTIFHUNG | ERRORONEXIT; never broadcast or disable timeout.
        return bool(self.user.SendMessageTimeoutW(self.window.hwnd, message, wparam, lparam, 0x23, timeout, C.byref(value)))

    def cancel(self, window, thread, timeout):
        if window != self.window or thread != self.thread:
            return False
        self.validate(thread)
        return self.emit(window, thread, WM_CANCELMODE, 0, 0, timeout)


class OwnedPoster(OwnedEmitter):
    """Only this test's exact class/PID/creation/thread/point can accept posts."""

    def __init__(self, window, thread, point):
        super().__init__(window, thread)
        self.point = point

    def post(self, window, thread, message, wparam, lparam):
        if window != self.window or thread != self.thread:
            raise SocialError("fixture_receiver_binding_mismatch")
        expected = {WM_MOUSEMOVE: (0, self.point), WM_LBUTTONDOWN: (1, self.point),
                    WM_LBUTTONUP: (0, self.point), WM_CANCELMODE: (0, 0)}
        if message not in expected or (wparam, lparam) != expected[message]:
            raise SocialError("fixture_message_denied")
        self.validate(thread)
        self.calls.append((message, wparam, lparam))
        return bool(self.user.PostMessageW(window.hwnd, message, wparam, lparam))

    def cancel(self, window, thread):
        return self.post(window, thread, WM_CANCELMODE, 0, 0)


def run_receiver(mode):
    user, kernel = libraries()
    callback_type = C.WINFUNCTYPE(C.c_ssize_t, C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t)

    class WindowClass(C.Structure):
        _fields_ = [("style", C.c_uint), ("proc", callback_type), ("class_extra", C.c_int), ("window_extra", C.c_int),
                    ("instance", C.c_void_p), ("icon", C.c_void_p), ("cursor", C.c_void_p), ("background", C.c_void_p),
                    ("menu", C.c_wchar_p), ("name", C.c_wchar_p)]

    class Point(C.Structure):
        _fields_ = [("x", C.c_long), ("y", C.c_long)]

    class Message(C.Structure):
        _fields_ = [("hwnd", C.c_void_p), ("message", C.c_uint), ("wparam", C.c_size_t), ("lparam", C.c_ssize_t),
                    ("time", C.c_uint32), ("point", Point), ("private", C.c_uint32)]

    signatures = {
        "DefWindowProcW": ([C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t], C.c_ssize_t),
        "RegisterClassW": ([C.POINTER(WindowClass)], C.c_uint16),
        "CreateWindowExW": ([C.c_uint32, C.c_wchar_p, C.c_wchar_p, C.c_uint32] + [C.c_int] * 4 + [C.c_void_p] * 4, C.c_void_p),
        "GetMessageW": ([C.POINTER(Message), C.c_void_p, C.c_uint, C.c_uint], C.c_int),
        "TranslateMessage": ([C.POINTER(Message)], C.c_int),
        "DispatchMessageW": ([C.POINTER(Message)], C.c_ssize_t),
    }
    for name, (args, returns) in signatures.items():
        getattr(user, name).argtypes, getattr(user, name).restype = args, returns

    @callback_type
    def receive(hwnd, message, wparam, lparam):
        if message in {WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_CANCELMODE}:
            print(json.dumps(dict(message=message, wparam=wparam, lparam=lparam)), flush=True)
            if (message == WM_LBUTTONDOWN and mode == "hang-down") or (message == WM_LBUTTONUP and mode == "hang-up"):
                time.sleep(5)
            return 0  # Zero is a normal handler result, not an API failure.
        return user.DefWindowProcW(hwnd, message, wparam, lparam)

    name = f"WeChatClickFixture_{os.getpid()}"
    instance = kernel.GetModuleHandleW(None)
    window_class = WindowClass()
    window_class.name, window_class.proc = name, receive
    window_class.instance = instance
    if not user.RegisterClassW(C.byref(window_class)):
        raise C.WinError(C.get_last_error())
    # No ShowWindow, activation, UIA, mouse movement, or keyboard input.
    hwnd = user.CreateWindowExW(0, name, "", 0, 0, 0, 640, 480, None, None, instance, None)
    if not hwnd:
        raise C.WinError(C.get_last_error())
    owner = C.c_uint32()
    thread = user.GetWindowThreadProcessId(hwnd, C.byref(owner))
    print(json.dumps(dict(hwnd=hwnd, pid=os.getpid(), created_filetime=creation_time(kernel, kernel.GetCurrentProcess()),
                          thread=thread, environment_prefix=sys.prefix, python_executable=sys.executable,
                          arguments=sys.argv[1:], versions={name: version(name) for name in ("ai-agent", "cryptography")})), flush=True)
    if mode == "queue-barrier":
        if sys.stdin.buffer.read(1) != b"1":
            return
    message = Message()
    while user.GetMessageW(C.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(C.byref(message))
        user.DispatchMessageW(C.byref(message))


if __name__ == "__main__":
    run_receiver(sys.argv[1])
