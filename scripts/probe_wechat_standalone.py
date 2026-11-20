"""Independent filehelper window identity preparation, without input/submission."""

import argparse
import asyncio
import ctypes as C
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.background_contract import (
    BMP_MARKER,
    CONTROL_MARKER,
    COVERED_MARKER,
    DEFERRED_MARKER,
    RETURN_MARKER,
    SOURCE_MARKER,
    VALUE_MARKER,
)
from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.background_process import BackgroundWorker
from wechat_agent.bridges.wechat.click_geometry import geometry_report, parse_geometry
from wechat_agent.bridges.wechat.contracts import AccountBinding, ConversationBinding, WindowBinding
from wechat_agent.bridges.wechat.keyprobe import remote_bytes
from wechat_agent.bridges.wechat.send_process import KillJob, python_worker_command, unique_object
from wechat_agent.bridges.wechat.sender import AccessibilityLease, InputObservation
from wechat_agent.bridges.wechat.standalone_identity import (
    inspect_button_action_route,
    inspect_input_value_routes,
    verify_standalone_filehelper,
)
from wechat_agent.social.models import OutgoingAction, SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, NativeMemory
    from .wechat_pinned_image import PinnedImage
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, NativeMemory
    from wechat_pinned_image import PinnedImage


WORKER_SCRIPT = Path(__file__).with_name("wechat_standalone_worker.py")

def surface_snapshot(window, *, inspect_invoke=False):
    command = [str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
               "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("wechat_standalone_surface.ps1")),
               "-TargetPid", str(window.pid), "-TargetHwnd", str(window.hwnd)]
    if inspect_invoke:
        command.append("-InspectInvokeOnly")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    job = None
    try:
        job = KillJob(process.pid)
        raw, _ = process.communicate(timeout=15)
        if process.returncode or len(raw) > 16384:
            raise SocialError("standalone_surface_not_verified")
        result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
        fields = {"runtime_ids", "input_token", "comparison", "geometry",
                  "root_class", "root_matches_filehelper", "qt_has_keyboard_focus"}
        if inspect_invoke:
            fields.add("button_invoke_supported")
        if (type(result) is not dict or set(result) != fields
                or inspect_invoke and type(result.get("button_invoke_supported")) is not bool
                or result["root_class"] != "mmui::ChatSingleWindow" or result["root_matches_filehelper"] is not True):
            raise SocialError("standalone_surface_shape_invalid")
        return result
    finally:
        if job:
            job.terminate()
        elif process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        if job:
            active = job.active()
            job.close()
            if active:
                raise SocialError("standalone_surface_child_not_reaped")


async def worker_readiness(window, binding, fingerprint, *, value_mode=False, deferred_focus=False, source_diagnostic=False,
                           bmp_mode=False, return_mode=False, covered_mode=False):
    method = "utf16_bmp_covered" if covered_mode else "utf16_bmp_return" if return_mode else "utf16_bmp_messages" if bmp_mode else "uia_source_diagnostic" if source_diagnostic else "uia_deferred_focus" if deferred_focus else "uia_value_once" if value_mode else "utf16_messages"
    marker = COVERED_MARKER if covered_mode else RETURN_MARKER if return_mode else BMP_MARKER if bmp_mode else SOURCE_MARKER if source_diagnostic else DEFERRED_MARKER if deferred_focus else VALUE_MARKER if value_mode else CONTROL_MARKER
    worker = BackgroundWorker(python_worker_command(WORKER_SCRIPT,
        "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
        "--account-fingerprint", fingerprint,
        "--input-method", method))
    try:
        async with asyncio.timeout(25):
            await worker.start("standalone-readonly-preparation", binding,
                OutgoingAction("standalone-readonly-preparation", binding.scope, "local-readonly", marker))
            return (await worker._rpc("prepare"))["readiness"]
    finally:
        if not await worker.stop():
            raise SocialError("standalone_background_worker_not_reaped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-readiness", action="store_true")
    parser.add_argument("--value-mode", action="store_true")
    parser.add_argument("--deferred-focus", action="store_true")
    parser.add_argument("--source-diagnostic", action="store_true", help="Prepare the fixed input-only source diagnostic; never writes")
    parser.add_argument("--bmp-mode", action="store_true", help="Prepare the accepted printable-BMP window-message scope; never writes")
    parser.add_argument("--return-mode", action="store_true", help="Prepare the BMP window-Return submit candidate; never writes")
    parser.add_argument("--covered-mode", action="store_true", help="Prepare the same Return route with active-app occlusion required; never writes")
    parser.add_argument("--diagnostics-only", action="store_true", help="Read-only failure preservation may observe an already-foreground target; never grants background readiness")
    parser.add_argument("--inspect-value-route", action="store_true", help="Read pinned setText/interface_cast code locations; never invoke them")
    parser.add_argument("--inspect-invoke-route", action="store_true", help="Read whether the bound send button exposes InvokePattern; never invoke it")
    args = parser.parse_args()
    if args.diagnostics_only and args.worker_readiness:
        parser.error("failure diagnostics cannot grant worker readiness")
    if sum((args.value_mode, args.deferred_focus, args.source_diagnostic, args.bmp_mode, args.return_mode, args.covered_mode)) > 1:
        parser.error("choose one fixed input mode")
    prior = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if (prior.get("status") != "header_key_verified" or prior.get("header_hmac_verified") is not True
            or prior.get("active_account_matched") is not True):
        parser.error("authenticated original account required")
    output = dict(status="started", input_posts=0, submit_posts=0, foreground_activations=0,
                  real_keyboard_or_mouse_calls=0, ledger_writes=0, model_requests=0)
    output["diagnostics_only"] = args.diagnostics_only
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        memory = NativeMemory(args.pid, hwnd=args.hwnd, activate=True)
        lease = observer = locks = None
        try:
            if not DLL.is_file():
                output["supported_version"] = DLL.parent.name
                output["installed_version_directories"] = sorted(
                    item.name for item in DLL.parent.parent.iterdir()
                    if item.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", item.name))[:8]
                raise SocialError("standalone_supported_version_missing")
            facts = memory.preflight()
            memory.u.IsIconic.argtypes, memory.u.IsIconic.restype = [C.c_void_p], C.c_int
            if memory.u.IsIconic(args.hwnd):
                raise SocialError("standalone_window_minimized")
            username, _, config = read_identity(memory, DLL.read_bytes(), configured_roots())
            fingerprint = hashlib.sha256(username.encode()).hexdigest()
            if fingerprint != prior.get("account_fingerprint"):
                raise SocialError("standalone_account_mismatch")
            window = WindowBinding(args.pid, int(facts["created_filetime"]), args.hwnd,
                                   facts["exe_sha256"], facts["dll_sha256"])
            binding = ConversationBinding(AccountBinding(username, "standalone-readiness", username, window,
                "authenticated-native-account"), "filehelper", "dm", "standalone-readiness")
            locks = writer_locks(binding)
            if not args.diagnostics_only:
                observer = BackgroundObserver(args.pid)
                observer.start()

            def record(stage, original):
                journal.write(json.dumps(dict(stage=stage, original=original)) + "\n")
                journal.flush()
                os.fsync(journal.fileno())

            # Reuse the already-open original-generation handle for byte ownership.
            class OpenMemory:
                def preflight(self):
                    memory.validate()
                    return facts

                def __getattr__(self, name):
                    return getattr(memory, name)

                def close(self):
                    pass  # Outer owner independently reads back and closes.

            output.update(window=facts, gate_before=memory.read(), account_matched=True)
            lease = AccessibilityLease(OpenMemory(), window, record)
            lease.open()
            data = surface_snapshot(window, inspect_invoke=args.inspect_invoke_route)
            if args.inspect_invoke_route:
                output["button_invoke_supported"] = data["button_invoke_supported"]
                output["button_action_route"] = inspect_button_action_route(memory, data["runtime_ids"]["button"],
                    PinnedImage(DLL.read_bytes()))
            native = verify_standalone_filehelper(memory, data["runtime_ids"]["chat_input"],
                account_reader=lambda: remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii"),
                expected_fingerprint=fingerprint)
            if args.inspect_value_route:
                output["value_routes"] = inspect_input_value_routes(memory, data["runtime_ids"]["chat_input"],
                    PinnedImage(DLL.read_bytes()))
            comparison = data["comparison"]
            output.update(native=native, both_patterns_empty=(comparison["value_empty"]
                and comparison["text_status"] == "read" and comparison["text_empty"] and comparison["exact_equal"]),
                root_matches_filehelper=data["root_matches_filehelper"], status="standalone_identity_verified")
            output["draft_diagnostics"] = comparison
            output["draft_matches_control_marker"] = (
                comparison["value_sha256"] == hashlib.sha256(CONTROL_MARKER.encode()).hexdigest())
            output["draft_matches_value_marker"] = (
                comparison["value_sha256"] == hashlib.sha256(VALUE_MARKER.encode()).hexdigest())
            output["draft_matches_deferred_marker"] = (
                comparison["value_sha256"] == hashlib.sha256(DEFERRED_MARKER.encode()).hexdigest())
            output["qt_input_has_keyboard_focus"] = data["qt_has_keyboard_focus"]
            observation = InputObservation(username, "filehelper", window, "native_username", data["input_token"],
                False, comparison["value_empty"], comparison["value_sha256"])
            output["geometry_checks"] = geometry_report(parse_geometry(data["geometry"], observation))["checks"]
            output["window_geometry"] = {key: data["geometry"][key] for key in (
                "button_bounds", "client_bounds", "client_point", "dpi", "coordinate_space", "dpi_context_restored")}
            if args.worker_readiness:
                output["worker_readiness"] = asyncio.run(worker_readiness(window, binding, fingerprint,
                    value_mode=args.value_mode, deferred_focus=args.deferred_focus, source_diagnostic=args.source_diagnostic,
                    bmp_mode=args.bmp_mode, return_mode=args.return_mode, covered_mode=args.covered_mode))
            if observer:
                observer.check()
        except Exception as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else "standalone_readiness_failed")
        finally:
            if lease:
                output["provider_restored"] = lease.close()
                if not output["provider_restored"]:
                    output.update(status="stopped", reason="standalone_provider_not_restored")
            if observer:
                output["observer_released"] = observer.stop()
                output["observation"] = observer.report()
                if not output["observer_released"] or observer.failure or observer.target_seen:
                    output["status"] = "stopped"
            if locks:
                try:
                    locks.close()
                    output["locks_released"] = True
                except Exception:
                    output.update(status="stopped", reason="standalone_lock_cleanup_failed")
            try:
                if memory.handle:
                    output["gate_after"] = memory.read()
            except Exception:
                output.update(status="stopped", reason="standalone_final_gate_unknown")
            memory.close()
            output["recorded_at"] = datetime.now(UTC).isoformat()
            json.dump(output, destination, ensure_ascii=False, indent=2)
            destination.flush()
            os.fsync(destination.fileno())
    print(json.dumps(output))
    return 0 if output["status"] == "standalone_identity_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
