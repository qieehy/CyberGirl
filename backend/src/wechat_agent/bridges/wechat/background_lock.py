"""Fail-closed writer exclusion for a bounded background control experiment.

Acquisition order is project account/process-generation lock, then the pinned
upstream's named mutex. Abandonment requires reconciliation, never permission.
No native DLL is loaded until a lock is explicitly constructed on Windows.
"""

import ctypes as C
import hashlib
import math
import os
import threading
from contextlib import ExitStack

from ...social.models import SocialError


class MutexApi:
    def __init__(self):
        if os.name != "nt":
            raise SocialError("background_windows_required")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateMutexW", [C.c_void_p, C.c_int, C.c_wchar_p], C.c_void_p),
            ("WaitForSingleObject", [C.c_void_p, C.c_uint32], C.c_uint32),
            ("ReleaseMutex", [C.c_void_p], C.c_int),
            ("CloseHandle", [C.c_void_p], C.c_int),
        ):
            getattr(self.kernel, name).argtypes = args
            getattr(self.kernel, name).restype = result

    def create(self, name):
        return self.kernel.CreateMutexW(None, False, name)

    def wait(self, handle, timeout_ms):
        return self.kernel.WaitForSingleObject(handle, timeout_ms)

    def release(self, handle):
        return bool(self.kernel.ReleaseMutex(handle))

    def close(self, handle):
        return bool(self.kernel.CloseHandle(handle))


class NamedWriteLock:
    def __init__(self, name, *, timeout=2.0, api=None):
        if (not isinstance(name, str) or not name.startswith("Local\\") or len(name) > 200
                or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 2):
            raise ValueError("bounded local mutex required")
        self.name, self.timeout, self.api = name, timeout, api
        self.handle = self.owner = None
        self.used = False

    def __enter__(self):
        if self.used:
            raise SocialError("background_lock_already_used")
        self.used = True
        self.api = self.api or MutexApi()
        self.handle = self.api.create(self.name)
        if not self.handle:
            raise SocialError("background_lock_create_failed")
        try:
            result = self.api.wait(self.handle, math.ceil(self.timeout * 1000))
            if result in (0, 0x80):
                self.owner = threading.get_ident()
            if result != 0:
                raise SocialError("background_lock_abandoned" if result == 0x80
                                  else "background_lock_unavailable")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc):
        if self.owner is not None and self.owner != threading.get_ident():
            raise SocialError("background_lock_thread_changed")
        ok = True
        try:
            if self.owner is not None:
                ok = self.api.release(self.handle) is True
        finally:
            self.owner = None
            if self.handle:
                ok = self.api.close(self.handle) is True and ok
                self.handle = None
        if not ok:
            raise SocialError("background_lock_cleanup_failed")


def writer_lock_names(binding):
    # Include account + process creation, independent of DB epoch or service instance.
    window = binding.account.window
    key = hashlib.sha256(
        f"{binding.account.account_id}:{window.pid}:{window.created_filetime}".encode()
    ).hexdigest()
    return ("Local\\WeChatAgent.writer." + key, "Local\\whale_bg_send")


def writer_locks(binding, *, factory=NamedWriteLock):
    """Caller keeps the returned stack alive until its worker is reaped/restored."""
    stack = ExitStack()
    try:
        for name in writer_lock_names(binding):
            stack.enter_context(factory(name))
        return stack
    except BaseException:
        stack.close()
        raise
