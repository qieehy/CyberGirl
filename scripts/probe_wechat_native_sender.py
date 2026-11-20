"""Read-only native check and historical fixed-marker PoC entry.

No model, navigation, draft clearing or retry. Reusing the fixed operator request
cannot create another attempt. Diagnostic JSON contains hashes/counts only.
The discovered focus-only button path is disabled by the native backend.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    WindowBinding,
    digest,
)
from wechat_agent.bridges.wechat.send_process import JsonSendWorker
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import AccessibilityLease, WeChatSendAdapter
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import OutgoingAction, SocialError
from wechat_agent.social.service import SocialService

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from .wechat_filehelper_source import FilehelperSource
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from wechat_filehelper_source import FilehelperSource

MARKER = "W2B-20261102-native-once"
REQUEST = "w2b5-20261102-prepared-once"
LEDGER = Path(".state/social/wechat-sender-poc.sqlite3")


class NoModel:
    async def aclose(self):
        pass

    async def decide(self, *args, **kwargs):
        raise SocialError("model_forbidden_in_fixed_marker_probe")


def require_preinput_replacement(prior, expected_action_id):
    """Never issue another fixed marker after any other action's text permit."""
    for old in prior:
        if old["input_permitted"] and old["action_id"] != expected_action_id:
            raise SocialError("fixed_marker_already_had_input_permit")
        if not old["input_permitted"] and (old["delivery_state"] != "FAILED" or not old["finished"]
                or not old["worker_reaped"] or not old["restore_verified"]):
            raise SocialError("prior_preinput_attempt_requires_review")


def worker_command(window, fingerprint, *, read_only=False):
    command = (sys.executable, str(Path(__file__).with_name("wechat_native_worker.py")),
            "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
            "--account-fingerprint", fingerprint)
    return command + (("--read-only",) if read_only else ())


async def readonly(window, fingerprint, journal, *, prepare_focus=False):
    memory = NativeMemory(window.pid, activate=True, hwnd=window.hwnd, created_filetime=window.created_filetime)
    lease = AccessibilityLease(memory, window, journal)
    worker = JsonSendWorker(worker_command(window, fingerprint, read_only=not prepare_focus))
    reaped = True
    result = {}
    try:
        async with asyncio.timeout(25):
            lease.open()
            username, _, _ = read_identity(memory, DLL.read_bytes(), configured_roots())
            if hashlib.sha256(username.encode()).hexdigest() != fingerprint:
                raise SocialError("native_probe_account_mismatch")
            binding = ConversationBinding(AccountBinding(username, "readonly-no-database", username, window, "w2a-account-hmac"),
                                          "filehelper", "dm", "w2b4-native-page")
            action = OutgoingAction("readonly-native-capabilities", binding.scope, "local-readonly", MARKER)
            await worker.start("readonly-native-capabilities", binding, action)
            response = await worker._rpc("capabilities")
            data = response["observation"]
            result.update(status="native_backend_observed", identity_method=data["identity_method"],
                          empty=data["empty"], focused=data["focused"], draft_sha256=data["draft_sha256"],
                          input_actions=0, submit_attempts=0, timings=response.get("timings"))
            if prepare_focus:
                journal("FOCUS_PREPARE_INTENT", lease.original)
                await worker.prepare_input_focus()
                (await worker.inspect()).verify(binding, action)
                result["focused"] = True
                result["focus_prepared"] = True
    except Exception as exc:
        result.update(status="native_backend_rejected", error_type=type(exc).__name__)
        if isinstance(exc, SocialError):
            result["reason"] = exc.code
    finally:
        reaped = await worker.stop()
        result.update(worker_reaped=reaped, restore_verified=lease.close(restore=reaped))
    return result


async def send_once(window, fingerprint, record, *, foreground_ready=False):
    source = FilehelperSource(window, fingerprint)
    try:
        starts = source.baselines()  # Authenticate snapshots before queuing or enabling UI.
        binding = source.binding
        # A new named ledger isolates the PoC from the workbench/reader ledger.
        with Ledger(LEDGER) as ledger:
            store = BridgeStore(ledger)
            store.bind(binding, mode="draft")
            ledger.configure_owner(binding.scope, binding.account.self_id)
            state = SendState(ledger)
            request_id = REQUEST + ("-foreground" if foreground_ready else "")
            expected_action_id = digest([binding.scope.key(), request_id, "operator-send"])
            # Only supersede a known pre-input failure. Any prior text permit
            # keeps this channel closed; never turn UNKNOWN into another send.
            prior = ledger._db.execute("""SELECT s.*,o.state AS delivery_state FROM wechat_send_operations s
                JOIN outbox o USING(action_id) ORDER BY s.created_at""").fetchall()
            require_preinput_replacement(prior, expected_action_id)
            action_id = state.queue_marker(binding, request_id, MARKER)

            async def baselines(candidate):
                if candidate != binding:
                    raise SocialError("snapshot_binding_changed")
                source.validate()
                return starts

            def lease_factory(target, log):
                return AccessibilityLease(NativeMemory(target.pid, activate=True, hwnd=target.hwnd,
                                                      created_filetime=target.created_filetime), target, log)

            adapter = WeChatSendAdapter(ledger, binding, writer_lock_factory=writer_locks, worker_factory=lambda: JsonSendWorker(worker_command(window, fingerprint)),
                                        lease_factory=lease_factory, baseline_provider=baselines, preparation_timeout=15)
            before = ledger.counts()["attempts"]
            record("DISPATCH_INTENT", 0)
            async with SocialService(ledger, NoModel(), [adapter], dispatch_timeout=25) as service:
                outcome = await service.dispatch(action_id)
            operation = ledger._db.execute("SELECT * FROM wechat_send_operations WHERE action_id=?", (action_id,)).fetchone()
            result = dict(status="single_marker_attempt_finished", state=outcome["state"],
                          superseded_verified_preinput_failures=sum(not old["input_permitted"] for old in prior),
                          attempts_created=ledger.counts()["attempts"] - before,
                          input_permitted=bool(operation and operation["input_permitted"]),
                          submit_permitted=bool(operation and operation["submit_permitted"]),
                          worker_reaped=bool(operation and operation["worker_reaped"]),
                          restore_verified=bool(operation and operation["restore_verified"]), local_echo=False,
                          marker_sha256=hashlib.sha256(MARKER.encode()).hexdigest(),
                          baseline={name: asdict(cursor) for name, cursor in starts.items()})
            if operation:
                result["operation_reason"] = operation["reason"]
                result["stages"] = [row[0] for row in ledger._db.execute(
                    "SELECT stage FROM wechat_send_journal WHERE operation_id=? ORDER BY seq", (operation["operation_id"],))]
            if operation and operation["submit_permitted"] and operation["worker_reaped"] and operation["restore_verified"]:
                # One readback only. Absence, ambiguity or errors leave UNCONFIRMED.
                try:
                    await asyncio.sleep(1)
                    records = source.records(starts)
                    result["new_records_read"] = len(records)
                    result["local_echo"] = state.local_echo(binding, action_id, records)
                except Exception as exc:
                    result["echo_error_type"] = type(exc).__name__
                    if isinstance(exc, SocialError):
                        result["echo_reason"] = exc.code
            result["final_state"] = ledger.action_summary(action_id)["state"]
            result["model_decisions"] = ledger.counts()["decisions"]
            return result
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send-once", action="store_true")
    parser.add_argument("--prepare-focus", action="store_true", help="Only prepare the verified empty input, without typing or submission")
    parser.add_argument("--foreground-ready", action="store_true", help="Use the final fixed request after manual foreground preparation; requires all other attempts to have zero input permits")
    args = parser.parse_args()
    if args.send_once and args.prepare_focus:
        parser.error("focus-only preparation excludes sending")
    if args.foreground_ready and not args.send_once:
        parser.error("manual foreground preparation applies only to the fixed single send")
    previous = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if (previous.get("status") != "header_key_verified" or not previous.get("active_account_matched")
            or not previous.get("header_hmac_verified")):
        parser.error("authenticated account evidence required")
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    fingerprint = previous["account_fingerprint"]
    journal_path = args.output.with_suffix(".journal.jsonl")
    if args.output.exists() or journal_path.exists():
        parser.error("evidence exists")
    result = dict(status="started", mode="send_once" if args.send_once else "readonly", model_requests=0,
                  arbitrary_targets_enabled=False, keys_written=0, plaintext_database_files_written=0)
    started = time.monotonic()
    with args.output.open("x", encoding="utf-8") as output, journal_path.open("x", encoding="utf-8") as journal:
        def record(stage, original):
            journal.write(json.dumps(dict(stage=stage, original=original, at=time.time())) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        try:
            result.update(asyncio.run(send_once(window, fingerprint, record, foreground_ready=args.foreground_ready) if args.send_once else readonly(
                window, fingerprint, record, prepare_focus=args.prepare_focus)))
        except BaseException as exc:
            result.update(status="not_verified_or_delivery_unknown", error_type=type(exc).__name__)
            if isinstance(exc, SocialError):
                result["reason"] = exc.code
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            files = ["scripts/probe_wechat_native_sender.py", "scripts/wechat_native_worker.py", "scripts/wechat_native_ui.ps1",
                     "scripts/wechat_filehelper_source.py", "backend/src/wechat_agent/bridges/wechat/native_sender.py",
                     "backend/src/wechat_agent/bridges/wechat/qt_memory.py", "backend/src/wechat_agent/bridges/wechat/sender.py",
                     "backend/src/wechat_agent/bridges/wechat/send_state.py", "backend/src/wechat_agent/bridges/wechat/send_process.py",
                     "backend/src/wechat_agent/bridges/wechat/send_worker.py"]
            root = Path(__file__).resolve().parent.parent
            result["implementation_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    print(json.dumps(result))
    return 0 if result["status"] in {"native_backend_observed", "single_marker_attempt_finished"} and result.get("restore_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
