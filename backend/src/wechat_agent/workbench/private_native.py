"""Explicit selected private-chat observer; never constructs a generator or sender."""

import asyncio
import hashlib
import json
from pathlib import Path

from ..bridges.wechat.contracts import WindowBinding
from ..bridges.wechat.private_receive import (
    PrivateReaderSession,
    ensure_private_selection_current,
    private_scope,
)
from ..bridges.wechat.receive_process import JsonReaderWorker
from ..bridges.wechat.send_process import python_worker_command, unique_object
from ..social.models import SocialError
from .native import native_source_root


async def open_private_receiver(ledger, *, scope_file, pid, hwnd, created_filetime, initialize=False, media=False):
    root = native_source_root()
    from scripts import wechat_42013_profile as profile
    profile.activate()
    scope_path = await asyncio.to_thread(Path(scope_file).resolve)
    scope_raw = await asyncio.to_thread(scope_path.read_bytes)
    scope = private_scope(json.loads(scope_raw, object_pairs_hook=unique_object))
    ensure_private_selection_current(ledger, scope)
    proof_raw = await asyncio.to_thread(Path(scope["identity_evidence"]).read_bytes)
    proof = json.loads(proof_raw, object_pairs_hook=unique_object)
    if (proof.get("status") != "selected_private_identity_verified" or proof.get("account_matched") is not True
            or proof.get("selected_label_matched") is not True or proof.get("provider_restored") is not True
            or proof.get("account_fingerprint") != scope["account_fingerprint"]
            or proof.get("gate_after") != 0
            or proof.get("native", {}).get("canonical_private_chat_verified") is not True
            or proof.get("native", {}).get("stable") is not True
            or proof.get("native", {}).get("username_sha256") != hashlib.sha256(scope["username"].encode()).hexdigest()):
        raise SocialError("private_native_identity_evidence_required")
    window = WindowBinding(pid, created_filetime, hwnd, profile.EXE_SHA, profile.DLL_SHA)
    worker = JsonReaderWorker(python_worker_command(root / "scripts/wechat_private_reader_worker.py",
        "--private-scope-file", str(scope_path), "--scope-sha256", hashlib.sha256(scope_raw).hexdigest(),
        *(["--media"] if media else [])))
    receiver = PrivateReaderSession(ledger, worker, window, scope["account_fingerprint"], scope)
    try:
        await receiver.start(initialize=initialize)
        return receiver
    except BaseException:
        await receiver.close()
        raise
