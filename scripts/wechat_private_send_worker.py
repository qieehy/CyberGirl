"""Pinned background-only private sender; no screen capture or foreground input."""
# ruff: noqa: E402
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.wechat_42013_profile import activate

activate()
from scripts.probe_wechat_account import read_identity
from scripts.probe_wechat_database import configured_roots
from scripts.verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
from scripts.wechat_pinned_image import PinnedImage
from scripts.wechat_standalone_backend import StandaloneCapabilities, StandalonePipe
from scripts.wechat_standalone_worker import StandaloneSession, serve
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.contracts import AccountBinding, ConversationBinding, WindowBinding
from wechat_agent.bridges.wechat.directed_native import CLASS_ENTRY_RVA
from wechat_agent.bridges.wechat.keyprobe import remote_bytes
from wechat_agent.bridges.wechat.private_identity import selected_private_identity
from wechat_agent.bridges.wechat.private_receive import private_scope
from wechat_agent.bridges.wechat.private_send import PrivateInputObservation, require_private_target
from wechat_agent.bridges.wechat.send_process import unique_object
from wechat_agent.context import ConversationScope
from wechat_agent.social.models import OutgoingAction, SocialError, identifier


class PrivateCapabilities(StandaloneCapabilities):
    observation_type = PrivateInputObservation

    def observed_username(self):
        return self.surface.binding.username

    def _native(self):
        binding = self.surface.binding
        proof = selected_private_identity(self.surface.memory, self.ids["chat_input"],
            root_automation_sha256=hashlib.sha256(("ChatSingleWindow" + binding.username).encode()).hexdigest(),
            account_reader=self.surface.account_reader, expected_fingerprint=self.surface.fingerprint)
        if proof["username"] != binding.username:
            raise SocialError("private_native_target_changed")
        return proof

    def verify_cleanup(self):
        if not getattr(self, "allow_foreground", False):
            return super().verify_cleanup()
        # Real input focus can legitimately remain in the active chat window.
        # Hidden/background windows must still release pseudo focus.
        import ctypes as C

        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = C.c_void_p
        for attempt in range(3):
            self.guard()
            self.observe()
            focused = self.last_snapshot["qt_has_keyboard_focus"]
            if focused is False or (focused is True and self.user.GetForegroundWindow() == self.window.hwnd):
                return self.guard()
            if attempt < 2:
                time.sleep(0.01)
        return False


class PrivateSession(StandaloneSession):
    def __init__(self, window, scope, text_sha256, *, allow_control=False, allow_foreground=False):
        self.window, self.scope, self.text_sha256 = window, scope, text_sha256
        self.fingerprint, self.allow_control = scope["account_fingerprint"], allow_control
        self.backend = SimpleNamespace(binding=None, action=None)
        self.initializer = SimpleNamespace(operation_id=None, handle=self.initialize)
        self.sequence, self.used = 0, False
        self.capabilities = self.memory = self.pipe = self.observer = None
        self.input_method, self.message_mode = "workbench_background_bmp", True
        self.allow_foreground = allow_foreground

    def initialize(self, request):
        if (set(request) != {"version", "operation_id", "sequence", "command", "binding", "action"}
                or type(request["version"]) is not int or request["version"] != 1
                or type(request["sequence"]) is not int or request["sequence"] != 1 or request["command"] != "init"):
            raise SocialError("private_worker_initialization_denied")
        identifier(request["operation_id"])
        data = dict(request["binding"])
        account = dict(data.pop("account"))
        account["window"] = WindowBinding(**account["window"])
        binding = ConversationBinding(account=AccountBinding(**account), **data)
        data = dict(request["action"])
        data["scope"] = ConversationScope(**data["scope"])
        action = OutgoingAction(**data)
        require_private_target(binding, action, observed_account=binding.account.account_id,
                               observed_username=self.scope["username"], observed_window=self.window)
        if (hashlib.sha256(binding.account.account_id.encode()).hexdigest() != self.fingerprint
                or hashlib.sha256(action.text.encode()).hexdigest() != self.text_sha256):
            raise SocialError("private_worker_scope_mismatch")
        self.backend.binding, self.backend.action = binding, action
        self.initializer.operation_id = request["operation_id"]
        return dict(operation_id=request["operation_id"], sequence=1, result={"accepted": True})

    def prepare(self):
        if self.capabilities is None:
            self.memory = NativeMemory(self.window.pid, hwnd=self.window.hwnd, created_filetime=self.window.created_filetime)
            self.memory.preflight()
            account, _, config = read_identity(self.memory, DLL.read_bytes(), configured_roots())
            if account != self.backend.binding.account.account_id or hashlib.sha256(account.encode()).hexdigest() != self.fingerprint:
                raise SocialError("private_worker_account_changed")
            class_code = PinnedImage(DLL.read_bytes()).at(CLASS_ENTRY_RVA, 128, executable=True)
            self.observer = BackgroundObserver(self.window.pid, allow_target_foreground=self.allow_foreground)
            self.observer.start()
            self.pipe = StandalonePipe(self.window,
                surface_script=Path(__file__).with_name("wechat_private_send_surface.ps1"),
                root_sha256=hashlib.sha256(("ChatSingleWindow" + self.scope["username"]).encode()).hexdigest())
            self.capabilities = PrivateCapabilities(self.memory, self.window, self.backend.binding,
                lambda: remote_bytes(self.memory.read_bytes, config + 0x48, maximum=128).decode("ascii"),
                self.fingerprint, self.observer, class_code,
                self.pipe, return_mode=True, require_cover=not self.allow_foreground)
            self.capabilities.allow_foreground = self.allow_foreground
        self.capabilities.guard()
        observation, _, _ = self.capabilities.observe()
        observation.verify(self.backend.binding, self.backend.action, require_focus=False)
        self.capabilities.guard()
        return dict(account_and_target_verified=True, both_draft_patterns_empty=True,
                    window_messages_only=True, canonical_private_identity_verified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + key, type=int, required=True)
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--scope-sha256", required=True)
    parser.add_argument("--text-sha256", required=True)
    parser.add_argument("--allow-control", action="store_true")
    parser.add_argument("--allow-foreground", action="store_true")
    args = parser.parse_args()
    raw = args.scope_file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.scope_sha256:
        raise SocialError("private_worker_scope_file_changed")
    scope = private_scope(json.loads(raw, object_pairs_hook=unique_object))
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    serve(PrivateSession(window, scope, args.text_sha256, allow_control=args.allow_control,
                        allow_foreground=args.allow_foreground), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
