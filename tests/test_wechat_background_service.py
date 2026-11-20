"""Real service and original review/journal orchestration with synthetic native boundaries."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

from scripts import probe_wechat_background_service as host
from tests.test_wechat_background_completion import clean_result, finished
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_background_sender import Worker
from wechat_agent.bridges.wechat.background_completion import record_background_completion
from wechat_agent.bridges.wechat.background_contract import COVERED_MARKER
from wechat_agent.bridges.wechat.contracts import DeliveryEvidence
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger


def test_service_host_uses_original_reviews_counts_and_one_native_worker(tmp_path, monkeypatch):
    path = tmp_path / "original.db"
    evidence = tmp_path / "account.json"
    evidence.write_text(json.dumps(dict(status="header_key_verified", header_hmac_verified=True,
        active_account_matched=True, account_fingerprint="a" * 64)), encoding="utf-8")
    with Ledger(path) as ledger:
        op, action = finished(ledger)
        record_background_completion(ledger, op, clean_result(), reaped=True, restored=True, locks_released=True)
        sha = hashlib.sha256(COVERED_MARKER.encode()).hexdigest()
        store = BridgeStore(ledger)
        store.record_evidence(DeliveryEvidence("echo", action.action_id, BINDING.scope, BINDING.generation,
            "local_echo", op, sha, "native-echo"))
        store.record_evidence(DeliveryEvidence("phone", action.action_id, BINDING.scope, BINDING.generation,
            "manual_confirmation", "synthetic-phone", sha))
    checkpoints = {"message_0.db": dict(shard="selected", start=dict(sort_seq=5, local_id=3), local_fence=3)}
    log, events, output = [], [], {}
    class Reader:
        binding = BINDING
        catalog = SimpleNamespace(checkpoints=lambda: checkpoints)
        def __init__(self):
            self.worker = SimpleNamespace(process=SimpleNamespace(pid=100), rpc=self.rpc)
        async def start(self):
            return checkpoints
        async def rpc(self, command):
            assert command == "manifest"
            return {"shards": {"message_0.db": dict(tail=dict(sort_seq=5, local_id=3), local_fence=3)}}
        async def poll(self):
            return dict(fresh=1, replay=0, pages=1, more=False)
        async def close(self):
            log.append("reader_closed")
            return True
    async def readiness(*args):
        return {key: True for key in {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
            "standalone_identity_verified", "background_observer_active", "no_capture", "button_not_pressed",
            "class_entry_matches_pinned_code", "covered_by_other_app"}}
    async def echo(*args):
        return True
    def factory(window, fingerprint, *, record):
        worker = Worker(log)
        worker.record = record
        return worker
    monkeypatch.setattr(host, "LEDGER", path)
    monkeypatch.setattr(host, "new_receiver", lambda *_: Reader())
    monkeypatch.setattr(host, "readonly_ready", readiness)
    monkeypatch.setattr(host, "native_worker", factory)
    monkeypatch.setattr(host, "writer_locks", lambda *_: SimpleNamespace(close=lambda: log.append("unlock")))
    monkeypatch.setattr(host, "NativeMemory", lambda *_a, **_kw: None)
    monkeypatch.setattr(host, "AccessibilityLease", lambda *_: SimpleNamespace(open=lambda: None, close=lambda **_: True))
    monkeypatch.setattr(host, "collect_echo", echo)
    args = SimpleNamespace(match_account_evidence=evidence, pid=BINDING.account.window.pid,
        hwnd=BINDING.account.window.hwnd, created_filetime=BINDING.account.window.created_filetime,
        restart=False, send_reviewed_once=True, approval_reference="fixed-batch-review", review_unknown_action=[],
        uncertainty_review_reference=None)
    asyncio.run(host.run(args, output, lambda kind, **kw: events.append(kind)))
    assert output["status"] == "background_service_echo_observed"
    assert output["confirmed_replay_checks"] == 1
    assert output["reader_reaped"] and output["active_operations"] == 0 and output["integrity_ok"]
    assert output["ending_counts"] == dict(operations=2, inputs=2, submits=2)
    assert output["dispatch_state"] == "UNCONFIRMED" and output["clean_control_recorded"]
    assert log.count("keys") == 1 and log[-1] == "reader_closed"
    assert events == ["background_send_echo_observed"]
