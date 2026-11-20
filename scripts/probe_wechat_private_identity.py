"""Read identity of one operator-selected private window; zero text/model/send operations."""

import argparse
import ctypes as C
import hashlib
import json
import os
import subprocess
from pathlib import Path

from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.contracts import AccountBinding, ConversationBinding, WindowBinding
from wechat_agent.bridges.wechat.send_process import KillJob
from wechat_agent.bridges.wechat.sender import AccessibilityLease
from wechat_agent.social.models import SocialError


def surface(window, label):
    command = [str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
               "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("wechat_private_identity_surface.ps1")),
               "-TargetPid", str(window.pid), "-TargetHwnd", str(window.hwnd), "-ExpectedLabel", label]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    job = None
    try:
        job = KillJob(process.pid)
        raw, _ = process.communicate(timeout=15)
        if process.returncode or len(raw) > 4096:
            raise SocialError("private_surface_not_verified")
        value = json.loads(raw.decode("utf-8-sig"))
        if set(value) != {"input_runtime_ids", "root_runtime_ids", "root_automation_sha256", "selected_label_matched"} or value["selected_label_matched"] is not True:
            raise SocialError("private_surface_shape_invalid")
        return value
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
                raise SocialError("private_surface_child_not_reaped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--selection-reference", required=True)
    parser.add_argument("--account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-target-output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.pid, args.hwnd, args.created_filetime) <= 0 or not 1 <= len(args.label) <= 128:
        parser.error("explicit current window and selected label required")
    if args.output.exists() or args.output.with_suffix(".journal.jsonl").exists() or args.private_target_output.exists():
        parser.error("outputs must be fresh")
    reference = json.loads(args.account_evidence.read_text(encoding="utf-8"))
    if not (reference.get("status") == "header_key_verified" and reference.get("header_hmac_verified") is True and reference.get("active_account_matched") is True):
        parser.error("authenticated original account required")
    from scripts.wechat_42013_profile import activate
    activate()
    from scripts.probe_wechat_account import read_identity
    from scripts.probe_wechat_database import configured_roots
    from scripts.verify_wechat_accessibility import DLL, NativeMemory
    from wechat_agent.bridges.wechat.keyprobe import remote_bytes
    from wechat_agent.bridges.wechat.private_identity import selected_private_identity

    result = dict(status="started", chat_text_reads=0, inputs=0, submits=0, model_calls=0, ledger_writes=0)
    memory = lease = locks = observer = None
    target = None
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        def record(stage, original):
            journal.write(json.dumps(dict(stage=stage, original=original)) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        try:
            memory = NativeMemory(args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime, activate=True)
            facts = memory.preflight()
            memory.u.IsIconic.argtypes, memory.u.IsIconic.restype = [C.c_void_p], C.c_int
            if memory.u.IsIconic(args.hwnd):
                raise SocialError("private_identity_window_minimized")
            account, _, config = read_identity(memory, DLL.read_bytes(), configured_roots())
            expected = reference["account_fingerprint"]
            if hashlib.sha256(account.encode()).hexdigest() != expected:
                raise SocialError("private_identity_account_mismatch")
            window = WindowBinding(args.pid, args.created_filetime, args.hwnd, facts["exe_sha256"], facts["dll_sha256"])
            selection = ConversationBinding(AccountBinding(account, "r3-identity-only", account, window, "r3-keyless-account"),
                                            "operator-selected-identity", "dm", args.selection_reference)
            locks = writer_locks(selection)
            observer = BackgroundObserver(args.pid)
            observer.start()
            observer.check()
            result["window"] = facts
            class OpenMemory:
                def preflight(self):
                    memory.validate()
                    return facts

                def __getattr__(self, name):
                    return getattr(memory, name)

                def close(self):
                    pass  # The outer owner independently reads the gate and closes this handle.

            lease = AccessibilityLease(OpenMemory(), window, record)
            lease.open()
            before = surface(window, args.label)
            native = selected_private_identity(memory, before["input_runtime_ids"], root_automation_sha256=before["root_automation_sha256"],
                account_reader=lambda: remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii"), expected_fingerprint=expected)
            after = surface(window, args.label)
            if after != before:
                raise SocialError("private_identity_surface_changed")
            observer.check()
            target = dict(version=1, account_fingerprint=expected, username=native.pop("username"), kind="dm",
                mode="observe", model_allowed=False, send_allowed=False,
                evidence_ref="r3-selected-native:" + native["username_sha256"], operator_label=args.label,
                selection_reference=args.selection_reference, identity_evidence=str(args.output.resolve()))
            result.update(status="selected_private_identity_verified", native=native, account_matched=True,
                          account_fingerprint=expected, selected_label_matched=True)
        except Exception as exc:
            result.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else type(exc).__name__)
        finally:
            if lease:
                result["provider_restored"] = lease.close()
            if observer:
                result["observer_stopped"] = observer.stop()
                result["observation"] = observer.report()
            if locks:
                locks.close()
                result["locks_released"] = True
            if memory:
                if memory.handle and lease:
                    result["gate_after"] = memory.read()
                memory.close()
            if target and (result.get("provider_restored") is not True or result.get("observer_stopped") is not True
                    or result["observation"]["failure"] or result["observation"]["foreground_target_seen"]
                    or result.get("gate_after") != 0):
                result.update(status="stopped", reason="private_identity_cleanup_incomplete")
            if result["status"] == "selected_private_identity_verified":
                with args.private_target_output.open("x", encoding="utf-8") as private_file:
                    json.dump(target, private_file, ensure_ascii=False, indent=2)
                    private_file.flush()
                    os.fsync(private_file.fileno())
                result["private_target_file"] = str(args.private_target_output.resolve())
            json.dump(result, destination, ensure_ascii=False, indent=2)
            destination.flush()
            os.fsync(destination.fileno())
    print(json.dumps(result))
    return 0 if result["status"] == "selected_private_identity_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
