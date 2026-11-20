"""BG1 observation: UI is read-only; an explicit owned provider lease is optional.

Uses the existing killable read-only UI worker and keyless account proof.
Observations do not authorize a message and do not open a new ledger action.
"""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.contracts import AccountBinding, ConversationBinding, WindowBinding
from wechat_agent.bridges.wechat.send_process import JsonSendWorker, python_worker_command
from wechat_agent.bridges.wechat.sender import AccessibilityLease
from wechat_agent.social.models import OutgoingAction, SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, NativeMemory
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, NativeMemory


async def inspect_ui(window, username, fingerprint, output):
    binding = ConversationBinding(AccountBinding(username, "background-readiness", username, window,
                                  "authenticated-native-account"), "filehelper", "dm", "native-page-readiness")
    worker = JsonSendWorker(python_worker_command(Path(__file__).with_name("wechat_native_worker.py"),
        "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
        "--account-fingerprint", fingerprint, "--read-only"))
    try:
        async with asyncio.timeout(20):
            await worker.start("background-readiness-only", binding,
                OutgoingAction("background-readiness-only", binding.scope, "local-readonly", "W2B-background-readiness-only"))
            first = await worker.warmup()  # Capabilities grants no input state or focus effect.
            draft = (await worker._rpc("draft_diagnostics"))["draft_diagnostics"]["comparison"]
            geometry = (await worker._rpc("geometry_diagnostics"))["geometry_diagnostics"]
            connection = (await worker._rpc("connection_diagnostics"))["connection_diagnostics"]
            last = await worker.warmup()
            if (last.input_token != first.input_token or last.draft_sha256 != first.draft_sha256
                    or last.empty != first.empty):
                raise SocialError("background_readiness_surface_changed")
            checks = geometry["checks"]
            spatial = ("button_size_bounded", "button_inside_client", "center_has_margin",
                       "client_mapping_consistent", "client_point_packable", "button_visible", "no_capture")
            output["readiness"] = dict(
                account_and_filehelper_verified=True,
                same_editor_and_button=True,
                both_draft_patterns_empty=(draft["value_empty"] and draft["text_status"] == "read"
                    and draft["text_empty"] and draft["exact_equal"]),
                background_geometry_verified=all(checks[k] is True for k in spatial),
                button_not_pressed=connection["pressed_bit_set"] is False,
                foreground_or_input_focus_required=False,
                real_sender_available=False,
            )
    finally:
        output["worker_reaped"] = await worker.stop()
        if not output["worker_reaped"]:
            raise SocialError("background_readiness_worker_not_reaped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True, help="Observed candidate; worker must prove its UIA/native main-page identity")
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owned-provider-lease", action="store_true", help="Temporarily enable the pinned accessibility byte with durable before-write/restoration evidence; no UI writes")
    args = parser.parse_args()
    previous = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if (previous.get("status") != "header_key_verified" or previous.get("active_account_matched") is not True
            or previous.get("header_hmac_verified") is not True):
        parser.error("authenticated original-account evidence required")
    output = dict(status="started", process_write_intents=0, ledger_writes=0, gate_write_intents=0,
                  pseudo_focus_posts=0, input_posts=0, submit_posts=0, model_requests=0)
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        memory = observer = lease = exclusion = None

        def record(stage, original):
            journal.write(json.dumps(dict(stage=stage, original=original, at=datetime.now(UTC).isoformat())) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            if stage in {"GATE_ENABLE_INTENT", "GATE_RESTORE_INTENT"}:
                output["process_write_intents"] += 1
                output["gate_write_intents"] += 1
        try:
            hwnd = args.hwnd
            memory = NativeMemory(args.pid, hwnd=hwnd)
            facts = memory.preflight()
            username, _, _ = read_identity(memory, DLL.read_bytes(), configured_roots())
            fingerprint = hashlib.sha256(username.encode()).hexdigest()
            if fingerprint != previous.get("account_fingerprint"):
                raise SocialError("background_readiness_account_mismatch")
            window = WindowBinding(args.pid, int(facts["created_filetime"]), hwnd,
                                   facts["exe_sha256"], facts["dll_sha256"])
            output.update(window_binding=facts, account_matched=True, gate_before=memory.read())
            observer = BackgroundObserver(args.pid)
            observer.start()
            if args.owned_provider_lease:
                binding = ConversationBinding(AccountBinding(username, "background-readiness", username, window,
                    "authenticated-native-account"), "filehelper", "dm", "native-page-readiness")
                exclusion = writer_locks(binding)
                lease = AccessibilityLease(NativeMemory(args.pid, activate=True, hwnd=hwnd,
                    created_filetime=window.created_filetime), window, record)
                lease.open()
            asyncio.run(inspect_ui(window, username, fingerprint, output))
            observer.check()
            output["status"] = "background_readiness_observed"
        except Exception as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError)
                          else "background_readiness_not_verified")
        finally:
            if lease is not None:
                output["provider_restored"] = lease.close(restore=output.get("worker_reaped", True))
                if not output["provider_restored"]:
                    output.update(status="stopped", reason="background_readiness_provider_not_restored")
            if exclusion is not None:
                try:
                    exclusion.close()
                    output["writer_locks_released"] = True
                except Exception:
                    output.update(status="stopped", reason="background_readiness_lock_cleanup_failed")
            if observer is not None:
                output["observer_released"] = observer.stop()
                output["observation"] = observer.report()
                if not output["observer_released"] or observer.failure or observer.target_seen:
                    output["status"] = "stopped"
            if memory is not None:
                try:
                    output["gate_after"] = memory.read()
                    if "gate_before" in output and output["gate_before"] != output["gate_after"]:
                        output.update(status="stopped", reason="background_readiness_gate_changed")
                except Exception:
                    output.update(status="stopped", reason="background_readiness_final_gate_unknown")
                finally:
                    memory.close()
            output["recorded_at"] = datetime.now(UTC).isoformat()
            json.dump(output, destination, ensure_ascii=False, indent=2)
            destination.flush()
            os.fsync(destination.fileno())
    print(json.dumps(output))
    return 0 if output["status"] == "background_readiness_observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
