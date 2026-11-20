"""Windows current-user DPAPI and a non-shared, non-inheritable ownership handle."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .errors import ErrorCode, ProviderError

_ENTROPY = b"WeChatAgent/codex/credentials/v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsProtection:
    def __init__(self) -> None:
        if os.name != "nt":
            raise ProviderError(ErrorCode.STORAGE)
        self._crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in ["CryptProtectData", "CryptUnprotectData"]:
            function = getattr(self._crypt, name)
            function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
            function.restype = wintypes.BOOL
        self._kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel.LocalFree.restype = ctypes.c_void_p

    def _transform(self, data: bytes, *, encrypt: bool) -> bytes:
        if not data or len(data) > 256 * 1024:
            raise ProviderError(ErrorCode.STORAGE)
        source = ctypes.create_string_buffer(data)
        entropy = ctypes.create_string_buffer(_ENTROPY)
        input_blob = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        entropy_blob = _Blob(len(_ENTROPY), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
        output_blob = _Blob()
        function = self._crypt.CryptProtectData if encrypt else self._crypt.CryptUnprotectData
        try:
            if not function(ctypes.byref(input_blob), None, ctypes.byref(entropy_blob), None, None, 1,
                            ctypes.byref(output_blob)):
                raise ProviderError(ErrorCode.STORAGE)
            if output_blob.cbData > 256 * 1024:
                raise ProviderError(ErrorCode.STORAGE)
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
                self._kernel.LocalFree(output_blob.pbData)
            ctypes.memset(source, 0, len(data))

    def protect(self, data: bytes) -> bytes:
        return self._transform(data, encrypt=True)

    def unprotect(self, data: bytes) -> bytes:
        return self._transform(data, encrypt=False)


class WindowsOwner:
    """ShareMode=0 denies a second opener and deletion while the owner is alive."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProviderError(ErrorCode.STORAGE)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._kernel.CreateFileW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._handle = None

    def acquire(self, path: Path) -> None:
        if self._handle is not None:
            raise ProviderError(ErrorCode.BUSY)
        handle = self._kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise ProviderError(ErrorCode.BUSY if error in {32, 33} else ErrorCode.STORAGE)
        self._handle = handle

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
