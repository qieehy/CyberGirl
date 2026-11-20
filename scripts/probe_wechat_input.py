"""Explicit bounded native capability test, with same-handle gate restoration.

This is project test tooling, not a sender. It cannot click, focus, type or submit.
"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.send_process import KillJob
from wechat_agent.bridges.wechat.sender import AccessibilityLease

if __package__:
    from .verify_wechat_accessibility import DLL_SHA, EXE_SHA, NativeMemory
else:
    from verify_wechat_accessibility import DLL_SHA, EXE_SHA, NativeMemory


class CollectorNotReaped(RuntimeError):
    pass


class InputProbeRejected(ValueError):
    """Only fixed reason codes may reach an evidence file."""

    code = "current_surface_not_filehelper_candidate"


def assess(data, window):
    if (data.get("status") != "observed" or data.get("pid") != window.pid or data.get("hwnd") != window.hwnd
            or data.get("created_filetime") != str(window.created_filetime)):
        raise ValueError("capability observation binding mismatch")
    tree = data["tree"]
    complete = not tree["bounded"] and not tree["errors"]
    nodes = tree["nodes"]
    inputs = [n for n in nodes if n["role"] == "chat_input"]
    buttons = [n for n in nodes if n["role"] == "send_button_candidate"]
    identity_hints = [n["index"] for n in nodes if n["automation_id_filehelper_token"] or n.get("automation_id_filehelper_md5") or any(
        n.get(k) for k in ("help_is_canonical_filehelper", "item_status_is_canonical_filehelper",
                          "item_type_is_canonical_filehelper", "value_is_canonical_filehelper",
                          "name_is_canonical_filehelper"))]
    return {"complete": complete, "node_count": len(nodes), "errors": len(tree["errors"]),
            "input_count": len(inputs), "inputs": inputs, "send_button_candidates": buttons,
            "selected_sessions": [n for n in nodes if n["selected"] and n["class_name"] == "mmui::ChatSessionCell"],
            "chat_info_candidates": [n for n in nodes if n["role"] == "chat_info_candidate"],
            "message_identity_shapes": [{k: n[k] for k in ("index", "class_name", "message_id_shape", "numeric_component_lengths")}
                                        for n in nodes if "message_id_shape" in n],
            "identity_hint_indexes": identity_hints,
            "canonical_md5_hint_indexes": [n["index"] for n in nodes if n.get("automation_id_filehelper_md5")],
            # Hints are evidence for review, never a capability grant.
            "canonical_identity_verified": False, "native_send_enabled": False}


def collect(window, output, *, runtime_callback=None):
    engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(engine), "-NoProfile", "-NonInteractive", "-File",
               str(Path(__file__).with_suffix(".ps1")), "-TargetPid", str(window.pid),
               "-TargetHwnd", str(window.hwnd), "-CreatedFiletime", str(window.created_filetime),
               "-OutputPath", str(output)]
    if runtime_callback is not None:
        command.append("-RuntimePipe")
    child = subprocess.Popen(command, stdout=subprocess.PIPE if runtime_callback else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    job = None
    try:
        job = KillJob(child.pid)
        if runtime_callback is not None:
            raw, _ = child.communicate(timeout=25)
        else:
            child.wait(timeout=25)
    finally:
        close_failed = False
        try:
            if job:
                job.close()
        except BaseException:
            close_failed = True
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
        except BaseException as exc:
            raise CollectorNotReaped("collector exit not verified") from exc
        if close_failed:
            raise CollectorNotReaped("collector job exit not verified")
    if child.returncode != 0:
        raise RuntimeError("capability collector failed")
    result = assess(json.loads(output.read_text(encoding="utf-8-sig")), window)
    if runtime_callback is not None:
        if not result["complete"] or result["input_count"] != 1:
            raise ValueError("native session observation incomplete")
        if len(result["selected_sessions"]) != 1:
            raise ValueError("selected native session is not unique")
        if (not result["inputs"][0].get("display_is_filehelper")
                or not result["selected_sessions"][0].get("name_contains_filehelper_label")):
            raise InputProbeRejected("current surface is not the filehelper research candidate")
        if len(raw) > 4096:
            raise ValueError("runtime pipe bound")
        transient = json.loads(raw.decode("utf-8-sig"))
        if (transient.get("pid") != window.pid or transient.get("hwnd") != window.hwnd
                or transient.get("created_filetime") != str(window.created_filetime)):
            raise ValueError("runtime pipe binding mismatch")
        result["native_object"] = runtime_callback(transient["runtime_ids"])
        native = result["native_object"]
        result["canonical_identity_verified"] = (native.get("canonical_conversation_verified") is True
                                                  and native.get("stable") is True
                                                  and native.get("matched_database_account") is True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activate-once", action="store_true")
    parser.add_argument("--native-session-object", action="store_true",
                        help="Read only the selected Qt accessible object through the pinned native cache")
    parser.add_argument("--native-session-model", action="store_true",
                        help="Inspect only bounded method locations of the selected row's model candidates")
    parser.add_argument("--native-session-parents", action="store_true",
                        help="Inspect bounded QObject parent chains of the selected row and chat input")
    parser.add_argument("--native-current-session", action="store_true",
                        help="Read the pinned business-session key of the current chat input page")
    parser.add_argument("--match-account-evidence", type=Path)
    args = parser.parse_args()
    if (args.native_session_model or args.native_session_parents) and not args.native_session_object:
        parser.error("native member/parent inspection requires --native-session-object")
    if args.native_current_session and (not args.native_session_parents or not args.match_account_evidence):
        parser.error("current-session inspection requires native parents and account evidence")
    if args.native_current_session and args.native_session_model:
        parser.error("current-session verification excludes speculative model inspection")
    output = args.output.resolve()
    journal_path, worker_path = output.with_suffix(".journal.jsonl"), output.with_suffix(".worker.json")
    if any(p.exists() for p in (output, journal_path, worker_path)):
        parser.error("evidence exists; choose new paths")
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    result = {"status": "started", "input_actions": 0, "external_messages": 0, "model_requests": 0,
              "mode": "activate_once" if args.activate_once else "readonly", "pid": window.pid,
              "hwnd": window.hwnd, "created_filetime": str(window.created_filetime)}
    with output.open("x", encoding="utf-8") as destination, journal_path.open("x", encoding="utf-8") as journal:
        def record(stage, original):
            journal.write(json.dumps({"stage": stage, "original": original, "at": datetime.now(UTC).isoformat()}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        memory = NativeMemory(window.pid, activate=args.activate_once, hwnd=window.hwnd,
                              created_filetime=window.created_filetime)
        lease = AccessibilityLease(memory, window, record)
        reaped = True
        try:
            if args.activate_once:
                lease.open()
            else:
                memory.preflight()
                result["original"] = memory.read()
            callback = None
            if args.native_session_object:
                from wechat_agent.bridges.wechat.qt_memory import (
                    selected_object,
                    verify_current_filehelper,
                )
                from wechat_agent.social.models import SocialError

                def callback(ids):
                    try:
                        if args.native_current_session:
                            if __package__:
                                from .probe_wechat_account import read_identity
                                from .probe_wechat_database import configured_roots
                                from .verify_wechat_accessibility import DLL
                            else:
                                from probe_wechat_account import read_identity
                                from probe_wechat_database import configured_roots
                                from verify_wechat_accessibility import DLL
                            from wechat_agent.bridges.wechat.keyprobe import remote_bytes

                            previous = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
                            if (previous.get("status") != "header_key_verified" or not previous.get("active_account_matched")
                                    or not previous.get("header_hmac_verified")):
                                raise SocialError("account_evidence_mismatch")
                            username, _, config = read_identity(memory, DLL.read_bytes(), configured_roots())
                            first = True

                            def account_reader():
                                nonlocal first
                                if first:
                                    first = False
                                    return username
                                return remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii")

                            return verify_current_filehelper(memory, ids["selected_session"], ids.get("chat_input"),
                                account_reader=account_reader, expected_account_fingerprint=previous.get("account_fingerprint"))
                        return selected_object(memory, ids["selected_session"], inspect_model=args.native_session_model,
                                               inspect_parents=args.native_session_parents,
                                               input_runtime_ids=ids.get("chat_input"))
                    except SocialError as exc:
                        detail = {"status": "not_verified", "reason": exc.code}
                        if hasattr(exc, "getter_rva"):
                            detail["getter_rva"] = exc.getter_rva
                        return detail

            result["observation"] = collect(window, worker_path, runtime_callback=callback)
            result["status"] = ("not_verified" if args.native_current_session
                                and not result["observation"]["canonical_identity_verified"] else "observed")
        except BaseException as exc:
            result.update(status="not_verified", error_type=type(exc).__name__)
            if isinstance(exc, InputProbeRejected):
                result["reason"] = exc.code
            reaped = not isinstance(exc, CollectorNotReaped)
        finally:
            result["collector_reaped"] = reaped
            result["restore_verified"] = lease.close(restore=reaped)
            result["original"] = lease.original if args.activate_once else result.get("original")
            if not result["restore_verified"]:
                result["status"] = "restore_failed"
            result["recorded_at"] = datetime.now(UTC).isoformat()
            root = Path(__file__).resolve().parent.parent
            components = ["scripts/probe_wechat_input.py", "scripts/probe_wechat_input.ps1",
                          "scripts/wechat_input_metadata.ps1", "scripts/uia_tree.ps1",
                          "scripts/verify_wechat_accessibility.py", "backend/src/wechat_agent/bridges/wechat/sender.py"]
            if args.native_session_object:
                components.append("backend/src/wechat_agent/bridges/wechat/qt_memory.py")
            if args.native_current_session:
                components.extend(("scripts/probe_wechat_account.py", "scripts/probe_wechat_database.py",
                                   "backend/src/wechat_agent/bridges/wechat/keyprobe.py"))
            result["implementation_sha256"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in components}
            json.dump(result, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
    print(json.dumps(result))
    return 0 if result["status"] == "observed" and result["restore_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
