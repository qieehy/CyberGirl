"""Trusted local selection proof must validate before an owned reader is created."""

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wechat_private_receive import selection
from wechat_agent.social.models import SocialError
from wechat_agent.workbench import private_native as module


@pytest.mark.parametrize("damage", [None, "account", "target", "canonical", "stable", "restore", "gate"])
def test_private_native_proof_rejection_precedes_worker(tmp_path, monkeypatch, damage):
    from scripts import wechat_42013_profile as profile

    scope = selection()
    proof = dict(status="selected_private_identity_verified", account_matched=True, selected_label_matched=True,
                 account_fingerprint=scope["account_fingerprint"], provider_restored=True, gate_after=0,
                 native=dict(username_sha256=hashlib.sha256(scope["username"].encode()).hexdigest(),
                             canonical_private_chat_verified=True, stable=True))
    if damage == "account":
        proof["account_fingerprint"] = "0" * 64
    elif damage == "target":
        proof["native"]["username_sha256"] = "0" * 64
    elif damage in {"canonical", "stable"}:
        proof["native"]["canonical_private_chat_verified" if damage == "canonical" else "stable"] = False
    elif damage == "restore":
        proof["provider_restored"] = False
    elif damage == "gate":
        proof["gate_after"] = 1
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    scope["identity_evidence"] = str(proof_path)
    scope_path = tmp_path / "selection.json"
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    created = []
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/wechat_private_reader_worker.py").write_text("# Synthetic owned worker, never executed.\n", encoding="utf-8")
    monkeypatch.setattr(profile, "activate", lambda: None)
    monkeypatch.setattr(module, "native_source_root", lambda: tmp_path)
    monkeypatch.setattr(module, "ensure_private_selection_current", lambda *_: None)

    def worker(command):
        assert command[command.index("--scope-sha256") + 1] == hashlib.sha256(scope_path.read_bytes()).hexdigest()
        created.append(command)
        return None

    async def start(**kwargs):
        assert kwargs == dict(initialize=True)

    async def close():
        pass

    monkeypatch.setattr(module, "JsonReaderWorker", worker)
    monkeypatch.setattr(module, "PrivateReaderSession", lambda *a, **k: SimpleNamespace(start=start, close=close))

    async def run():
        invocation = module.open_private_receiver(None, scope_file=scope_path, pid=123, hwnd=456,
            created_filetime=789, initialize=True)
        if damage:
            with pytest.raises(SocialError, match="identity_evidence_required"):
                await invocation
            assert not created
        else:
            await invocation
            assert len(created) == 1
    asyncio.run(run())


def test_changed_selection_hash_rejected_in_child_before_native_import(tmp_path):
    scope_path = tmp_path / "selection.json"
    scope_path.write_text(json.dumps(selection()), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/wechat_private_reader_worker.py"),
        "--private-scope-file", str(scope_path), "--scope-sha256", "0" * 64], capture_output=True, timeout=15)
    assert result.returncode == 2 and result.stdout == result.stderr == b""
