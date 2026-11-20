"""Application-owned native worker. Effects require the parent one-use protocol."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.keyprobe import remote_bytes
from wechat_agent.bridges.wechat.native_sender import NativeSendBackend
from wechat_agent.bridges.wechat.send_process import MAX_FRAME, unique_object
from wechat_agent.bridges.wechat.send_worker import serve
from wechat_agent.social.models import SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory


class UiPipe:
    """Blocking only inside a killable worker. Parent deadline bounds all IO."""

    def __init__(self, window, *, read_only=False):
        self.read_only = read_only
        engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(engine), "-NoProfile", "-NonInteractive", "-File",
            str(Path(__file__).with_name("wechat_native_ui.ps1")), "-TargetPid", str(window.pid),
            "-TargetHwnd", str(window.hwnd), "-CreatedFiletime", str(window.created_filetime)]
        if read_only:
            command.append("-ReadOnly")
        self.process = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.sequence = 0

    def rpc(self, command, **payload):
        if self.read_only and command not in {"observe", "draft_patterns", "click_geometry", "focus_state"}:
            raise SocialError("readonly_ui_command_denied")
        self.sequence += 1
        request = json.dumps(dict(sequence=self.sequence, command=command, **payload), separators=(",", ":")).encode() + b"\n"
        if len(request) > MAX_FRAME:
            raise SocialError("native_ui_request_bound")
        self.process.stdin.write(request)
        self.process.stdin.flush()
        raw = self.process.stdout.readline(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            raise SocialError("native_ui_response_bound")
        response = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if (type(response) is dict and response.get("error") == "native_ui_rejected"
                and type(response.get("line")) is int and 0 < response["line"] < 1000):
            surface_check = response.get("surface_check")
            surface_codes = {f"{role}_{check}" for role in ("input", "row", "page", "button")
                             for check in ("process", "class", "offscreen")}
            surface_codes |= {"row_selection", "input_page", "button_view", "input_view"}
            if type(surface_check) is str and surface_check in surface_codes:
                raise SocialError("native_surface_" + surface_check)
            checks = response.get("candidate_checks")
            fields = ("input_name_match", "row_name_match", "input_id_match", "input_enabled")
            if (type(checks) is dict and set(checks) == set(fields)
                    and all(type(checks[k]) is bool for k in fields) and not all(checks.values())):
                raise SocialError("filehelper_candidate_" + "_".join(str(int(checks[k])) for k in fields))
            raise SocialError("native_ui_rejected_at_" + str(response["line"]))
        if (type(response) is not dict or set(response) != {"sequence", "result"}
                or type(response["sequence"]) is not int or response["sequence"] != self.sequence):
            raise SocialError("native_ui_response_rejected")
        result = response["result"]
        if command not in {"observe", "draft_patterns", "click_geometry", "focus_state"} and result != {"accepted": True}:
            raise SocialError("native_ui_action_unknown")
        return result

    def observe(self):
        return self.rpc("observe")

    def draft_patterns(self, token):
        return self.rpc("draft_patterns", token=token)

    def click_geometry(self, token):
        return self.rpc("click_geometry", token=token)

    def focus_once(self, token):
        self.rpc("focus_once", token=token)

    def input_once(self, token, text):
        self.rpc("input_once", token=token, text=text)

    def submit_once(self, token):
        self.rpc("submit_once", token=token)


def make_backend(window, fingerprint, *, read_only=False, directed_trial=False, posted_trial=False, sequence_trial=False, physical_trial=False):
    if sum((directed_trial, posted_trial, sequence_trial, physical_trial)) > 1:
        raise SocialError("native_worker_trial_route_conflict")
    def opener(binding):
        memory = NativeMemory(window.pid, hwnd=window.hwnd, created_filetime=window.created_filetime)
        try:
            facts = memory.preflight()
            if facts["exe_sha256"] != window.exe_sha256 or facts["dll_sha256"] != window.dll_sha256:
                raise SocialError("native_worker_binary_changed")
            username, _, config = read_identity(memory, DLL.read_bytes(), configured_roots())
            if username != binding.account.account_id or hashlib.sha256(username.encode()).hexdigest() != fingerprint:
                raise SocialError("native_worker_account_changed")

            def account_reader():
                return remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii")

            return memory, account_reader, UiPipe(window, read_only=read_only)
        except BaseException:
            memory.close()
            raise

    if directed_trial or posted_trial or sequence_trial or physical_trial:
        from wechat_agent.bridges.wechat.directed_native import CLASS_ENTRY_RVA, NativeDirectedTrial

        if __package__:
            from .wechat_pinned_image import PinnedImage
        else:
            from wechat_pinned_image import PinnedImage

        backend_type = NativeDirectedTrial
        if posted_trial:
            from wechat_agent.bridges.wechat.posted_native import NativePostedTrial

            backend_type = NativePostedTrial
        if sequence_trial:
            from wechat_agent.bridges.wechat.sequence_native import NativeSequenceTrial

            backend_type = NativeSequenceTrial
        if physical_trial:
            from wechat_agent.bridges.wechat.physical_native import NativePhysicalTrial

            backend_type = NativePhysicalTrial
        return backend_type(window, fingerprint, opener,
            class_code=lambda: PinnedImage(DLL.read_bytes()).at(CLASS_ENTRY_RVA, 128, executable=True))
    return NativeSendBackend(window, fingerprint, opener)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--read-only", action="store_true")
    trials = parser.add_mutually_exclusive_group()
    trials.add_argument("--directed-fixed-trial", action="store_true")
    trials.add_argument("--posted-fixed-trial", action="store_true")
    trials.add_argument("--sequence-fixed-trial", action="store_true")
    trials.add_argument("--physical-fixed-trial", action="store_true")
    args = parser.parse_args()
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    try:
        serve(make_backend(window, args.account_fingerprint, read_only=args.read_only,
            directed_trial=args.directed_fixed_trial, posted_trial=args.posted_fixed_trial,
            sequence_trial=args.sequence_fixed_trial, physical_trial=args.physical_fixed_trial), sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except BaseException:
        return 1  # Never print account names, request text or provider errors.


if __name__ == "__main__":
    raise SystemExit(main())
