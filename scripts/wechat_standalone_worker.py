"""Killable, explicitly selected standalone B18 worker. Read-only by default."""

import argparse
import hashlib
import json
import sys
from contextlib import nullcontext
from dataclasses import asdict

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
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.background_process import BackgroundIntentClient
from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.directed_native import CLASS_ENTRY_RVA
from wechat_agent.bridges.wechat.keyprobe import remote_bytes
from wechat_agent.bridges.wechat.send_process import MAX_FRAME, unique_object
from wechat_agent.bridges.wechat.send_worker import WorkerSession
from wechat_agent.social.models import SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from .wechat_background_control import (
        BackgroundControl,
        StandaloneBmpControl,
        StandaloneCoveredControl,
        StandaloneDeferredControl,
        StandaloneIntegrationControl,
        StandaloneReturnControl,
        StandaloneSourceDiagnostic,
        StandaloneValueControl,
        WorkbenchControl,
    )
    from .wechat_background_native import BackgroundSurface, WorkbenchSurface
    from .wechat_pinned_image import PinnedImage
    from .wechat_standalone_backend import StandaloneCapabilities, StandalonePipe
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from wechat_background_control import (
        BackgroundControl,
        StandaloneBmpControl,
        StandaloneCoveredControl,
        StandaloneDeferredControl,
        StandaloneIntegrationControl,
        StandaloneReturnControl,
        StandaloneSourceDiagnostic,
        StandaloneValueControl,
        WorkbenchControl,
    )
    from wechat_background_native import BackgroundSurface, WorkbenchSurface
    from wechat_pinned_image import PinnedImage
    from wechat_standalone_backend import StandaloneCapabilities, StandalonePipe


class StandaloneSession:
    def __init__(self, window, fingerprint, *, allow_control=False, input_method="utf16_messages"):
        self.window, self.fingerprint, self.allow_control = window, fingerprint, allow_control
        self.backend = (WorkbenchSurface if input_method == 'workbench_background_bmp' else BackgroundSurface)(window, fingerprint, None)
        self.initializer = WorkerSession(self.backend)
        self.sequence = 0
        self.used = False
        self.capabilities = self.memory = self.pipe = self.observer = None
        if input_method not in {"utf16_messages", "utf16_bmp_messages", "utf16_bmp_return", "utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp", "uia_value_once", "uia_deferred_focus", "uia_source_diagnostic"}:
            raise SocialError("standalone_input_method_denied")
        self.input_method = input_method
        self.message_mode = input_method in {"utf16_messages", "utf16_bmp_messages", "utf16_bmp_return", "utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp"}

    def prepare(self):
        if self.capabilities is None:
            self.memory = NativeMemory(self.window.pid, hwnd=self.window.hwnd, created_filetime=self.window.created_filetime)
            self.memory.preflight()
            username, _, config = read_identity(self.memory, DLL.read_bytes(), configured_roots())
            if (hashlib.sha256(username.encode()).hexdigest() != self.fingerprint
                    or username != self.backend.binding.account.account_id):
                raise SocialError("standalone_worker_account_changed")
            self.observer = BackgroundObserver(self.window.pid)
            self.observer.start()
            self.pipe = StandalonePipe(self.window, allow_fixed_input=self.allow_control and not self.message_mode,
                expected_marker={"uia_deferred_focus": DEFERRED_MARKER, "uia_source_diagnostic": SOURCE_MARKER}.get(
                    self.input_method, VALUE_MARKER))
            self.capabilities = StandaloneCapabilities(self.memory, self.window, self.backend.binding,
                lambda: remote_bytes(self.memory.read_bytes, config + 0x48, maximum=128).decode("ascii"),
                self.fingerprint, self.observer,
                PinnedImage(DLL.read_bytes()).at(CLASS_ENTRY_RVA, 128, executable=True), self.pipe,
                value_mode=not self.message_mode,
                return_mode=self.input_method in {"utf16_bmp_return", "utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp"},
                require_cover=self.input_method in {"utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp"})
        capability = self.capabilities
        capability.guard()
        observation, _, _ = capability.observe()
        observation.verify(self.backend.binding, self.backend.action, require_focus=False)
        capability.guard()
        result = dict(account_and_filehelper_verified=True, both_draft_patterns_empty=True,
                    same_editor_and_button=True, standalone_identity_verified=True,
                    background_observer_active=True, no_capture=True, button_not_pressed=True,
                    class_entry_matches_pinned_code=True)
        if self.input_method in {"utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp"}:
            result["covered_by_other_app"] = True
        return result

    def handle(self, request, source, destination):
        if self.sequence == 0:
            response = self.initializer.handle(request)
            expected = {"utf16_messages": CONTROL_MARKER, "utf16_bmp_messages": BMP_MARKER,
                        "utf16_bmp_return": RETURN_MARKER, "utf16_bmp_covered": COVERED_MARKER, "utf16_bmp_integrated": self.backend.action.text, "workbench_background_bmp": self.backend.action.text, "uia_value_once": VALUE_MARKER,
                        "uia_deferred_focus": DEFERRED_MARKER, "uia_source_diagnostic": SOURCE_MARKER}[self.input_method]
            if (self.input_method == "utf16_bmp_integrated" and expected not in {INTEGRATED_MARKER, RESTART_MARKER}) or self.backend.action.text != expected:
                raise SocialError("standalone_marker_method_mismatch")
            self.sequence = 1
            return response
        if (self.used or type(request) is not dict
                or set(request) != {"version", "operation_id", "sequence", "command"}
                or request["version"] != 1 or type(request["version"]) is not int
                or request["operation_id"] != self.initializer.operation_id
                or type(request["sequence"]) is not int or request["sequence"] != self.sequence + 1):
            raise SocialError("standalone_worker_request_invalid")
        self.sequence = request["sequence"]
        if request["command"] == "prepare":
            result = {"readiness": self.prepare()}
        elif request["command"] == "run_background" and self.allow_control and self.capabilities is not None:
            self.used = True
            client = BackgroundIntentClient(source, destination, self.initializer.operation_id, self.sequence)
            control_type = {"utf16_messages": BackgroundControl, "utf16_bmp_messages": StandaloneBmpControl,
                            "utf16_bmp_return": StandaloneReturnControl, "utf16_bmp_covered": StandaloneCoveredControl, "utf16_bmp_integrated": StandaloneIntegrationControl, "workbench_background_bmp": WorkbenchControl,
                            "uia_value_once": StandaloneValueControl,
                            "uia_deferred_focus": StandaloneDeferredControl,
                            "uia_source_diagnostic": StandaloneSourceDiagnostic}[self.input_method]
            control = control_type(self.backend.binding, self.backend.action,
                observe=self.capabilities.observe, guard=self.capabilities.guard,
                transport=self.capabilities, record=client.record)
            result = {"control_result": asdict(control.run_once(write_guard=nullcontext()))}
            result.update(self.close())
        else:
            raise SocialError("standalone_worker_effect_not_permitted")
        return dict(operation_id=self.initializer.operation_id, sequence=self.sequence, result=result)

    def close(self):
        verified = True
        if self.pipe:
            try:
                self.pipe.close()
            except BaseException:
                verified = False
            self.pipe = None
        report = None
        if self.observer:
            verified = self.observer.stop() and verified
            report = self.observer.report()
            verified = verified and not self.observer.failure and not getattr(
                self.observer, "foreground_violation", self.observer.target_seen)
            self.observer = None
        if self.memory:
            self.memory.close()
            self.memory = None
        return dict(worker_local_cleanup_verified=bool(verified), observation=report)


def serve(session, source, destination):
    try:
        while True:
            raw = source.readline(MAX_FRAME + 1)
            if not raw:
                return
            if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                raise SocialError("standalone_worker_frame_bound")
            request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            terminal = False
            try:
                response = session.handle(request, source, destination)
            except SocialError as exc:
                terminal = True
                response = dict(operation_id=session.initializer.operation_id, sequence=request.get("sequence"),
                                result=dict(rejected_reason=exc.code))
            destination.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            destination.flush()
            if terminal or session.used:
                return
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--allow-control", action="store_true")
    parser.add_argument("--input-method", choices=("utf16_messages", "utf16_bmp_messages", "utf16_bmp_return", "utf16_bmp_covered", "utf16_bmp_integrated", "workbench_background_bmp", "uia_value_once", "uia_deferred_focus", "uia_source_diagnostic"), default="utf16_messages")
    args = parser.parse_args()
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    try:
        serve(StandaloneSession(window, args.account_fingerprint, allow_control=args.allow_control, input_method=args.input_method),
              sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except BaseException:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
