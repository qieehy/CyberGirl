import ctypes as C
from types import SimpleNamespace

import pytest

from scripts import wechat_background_native as native
from tests.test_wechat_bridge import WINDOW
from wechat_agent.social.models import SocialError


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def setup(monkeypatch):
    state = dict(pid=WINDOW.pid, thread=55, visible=True, iconic=False, class_changed=False,
                 observer_ok=True, posted=0, native_checks=0)

    def owner(_hwnd, pointer):
        C.cast(pointer, C.POINTER(C.c_uint32)).contents.value = state["pid"]
        return state["thread"]

    def check():
        if not state["observer_ok"]:
            raise SocialError("background_target_became_foreground")
        return True

    def posted(*_):
        state["posted"] += 1
        return True

    user = SimpleNamespace(
        GetWindowThreadProcessId=Function(owner),
        GetClassLongPtrW=Function(lambda *_: 100 + native.CLASS_ENTRY_RVA + int(state["class_changed"])),
        IsWindowVisible=Function(lambda *_: state["visible"]),
        IsIconic=Function(lambda *_: state["iconic"]),
        PostMessageW=Function(posted),
        SetThreadDpiAwarenessContext=Function(lambda *_: 123),
        ScreenToClient=Function(lambda *_: True),
    )
    memory = SimpleNamespace(u=user, base=100, validate=lambda: None,
                             read_bytes=lambda *_: b"x" * 128)
    surface = SimpleNamespace(window=WINDOW, memory=memory, fingerprint="f" * 64, account_reader=lambda: "fixture")
    capability = native.BackgroundNativeCapabilities(surface, SimpleNamespace(check=check), b"x" * 128)
    capability.ids = {"selected_session": [3, 1], "chat_input": [3, 2]}
    capability.token = "a" * 64

    def canonical(*_args, **_kwargs):
        state["native_checks"] += 1

    monkeypatch.setattr(native, "verify_current_filehelper", canonical)
    return capability, state


@pytest.mark.parametrize("change", ["pid", "thread", "visible", "iconic", "class_changed", "observer_ok"])
def test_target_change_prevents_actual_enqueue(monkeypatch, change):
    capability, state = setup(monkeypatch)
    assert capability.guard()
    state[change] = {"pid": WINDOW.pid + 1, "thread": 66, "visible": False,
                     "iconic": True, "class_changed": True, "observer_ok": False}[change]
    with pytest.raises(SocialError):
        capability.post(0x102, ord("A"), 1)
    assert state["posted"] == 0


def test_canonical_change_during_guard_cannot_post(monkeypatch):
    capability, state = setup(monkeypatch)

    def changed(*_args, **_kwargs):
        state["pid"] += 1

    monkeypatch.setattr(native, "verify_current_filehelper", changed)
    with pytest.raises(SocialError, match="receiver_changed"):
        capability.post(0x102, ord("A"), 1)
    assert state["posted"] == 0


def test_unprepared_or_non_allowlisted_message_cannot_post(monkeypatch):
    capability, state = setup(monkeypatch)
    with pytest.raises(SocialError, match="message_denied"):
        capability.post(0x6, 1, 0)  # WM_ACTIVATE is explicitly forbidden.
    capability.ids = None
    with pytest.raises(SocialError, match="not_prepared"):
        capability.post(0x102, ord("A"), 1)
    assert not state["posted"]
