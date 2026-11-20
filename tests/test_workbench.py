"""Real ledger/service paths with synthetic native and model boundaries only."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wechat_background_completion import clean_result, finished
from tests.test_wechat_background_control import BINDING
from tests.test_wechat_background_sender import Worker
from tests.test_wechat_sender import NoModel
from wechat_agent.bridges.wechat.background_completion import (
    record_background_completion,
    require_background_admission,
)
from wechat_agent.bridges.wechat.background_contract import COVERED_MARKER
from wechat_agent.bridges.wechat.background_readmission import READY_FIELDS
from wechat_agent.bridges.wechat.contracts import Cursor, DeliveryEvidence, SourceRecord
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.providers.codex import ErrorCode, ProviderError
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.social.service import SocialService
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.sender import WorkbenchBackgroundAdapter
from wechat_agent.workbench.service import LiveDraftGenerator, WeChatWorkbenchService


def seed(ledger):
    operation, action = finished(ledger)
    record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
    sha = hashlib.sha256(COVERED_MARKER.encode()).hexdigest()
    store = BridgeStore(ledger)
    store.record_evidence(DeliveryEvidence("echo-old", action.action_id, BINDING.scope, BINDING.generation,
                                        "local_echo", operation, sha, "old-native-message"))
    store.record_evidence(DeliveryEvidence("phone-old", action.action_id, BINDING.scope, BINDING.generation,
                                        "manual_confirmation", "old-phone", sha))
    store.baseline(BINDING, "selected", Cursor(0, 0))
    return add_source(ledger)


def add_source(ledger, n=1):
    record = SourceRecord(BINDING.account.account_id, BINDING.account.db_epoch, "filehelper", "dm", "selected",
                          n, n, BINDING.account.self_id, ledger.now(), ledger.now(), "手机消息" + str(n))
    BridgeStore(ledger).ingest(BINDING, "selected", Cursor(n - 1, n - 1), (record,))
    return record


class Sender:
    reviewed_unknowns = ()
    uncertainty_reference = None

    def __init__(self, ledger):
        self.ledger, self.calls, self.log = ledger, 0, []
        self.before_submit = None

    async def prepare(self, draft_id):
        return {k: True for k in READY_FIELDS}, require_background_admission

    async def dispatch(self, aid):
        self.calls += 1
        worker = Worker(self.log, pause=self.before_submit)

        def factory(*, record):
            worker.record = record
            return worker

        async def baseline(_):
            return {"selected": Cursor(0, 0)}

        adapter = WorkbenchBackgroundAdapter(self.ledger, BINDING, worker_factory=factory,
            lease_factory=lambda *_: SimpleNamespace(open=lambda: None, close=lambda **_: True),
            writer_lock_factory=lambda _: SimpleNamespace(close=lambda: None), baseline_provider=baseline)
        async with SocialService(self.ledger, NoModel(), [adapter], dispatch_timeout=25) as service:
            await service.dispatch(aid)
        assert adapter.last_run["clean_control_recorded"]
        row = self.ledger._db.execute("SELECT * FROM wechat_send_operations WHERE action_id=?", (aid,)).fetchone()
        BridgeStore(self.ledger).record_evidence(DeliveryEvidence("echo-" + aid, aid, BINDING.scope, BINDING.generation,
            "local_echo", row["operation_id"], row["content_sha256"], "native-" + aid))

    async def aclose(self):
        pass


def workbench(ledger, *, generator=None):
    async def close():
        BridgeStore(ledger).unbind(BINDING)
        return True

    receiver = SimpleNamespace(binding=BINDING, close=close)
    wb = WeChatWorkbenchService(ledger, receiver, sender=Sender(ledger), model="test-model", generator=generator)
    wb.reading = True
    return wb


async def generate(wb, source, text="原始回复🙂"):
    calls = []

    async def generator(*args):
        calls.append(args)
        return Decision(ActionKind.REPLY, text)

    wb.generator = generator
    payload = dict(request_id="generation", source_message_id=source.message_id,
                   expected_message_revision=wb.state.revision(), expected_persona_version=1)
    draft = await wb.generate(**payload)
    await asyncio.gather(*wb.tasks)
    assert (await wb.generate(**payload))["draft_id"] == draft["draft_id"]
    assert len(calls) == 1
    return wb.state.draft(draft["draft_id"])


def test_operator_selection_edit_confirmation_echo_phone_and_no_replay(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            assert wb.snapshot()["messages"][0]["disposition"] == "self_echo"
            before = ledger.counts()
            draft = await generate(wb, source)
            assert ledger.counts() == before  # no fabricated incoming or outbox
            assert draft["send_error"] == "background_supplementary_character_unsupported"
            draft = wb.state.edit(draft["draft_id"], "edit", 1, "编辑后的中文回复")
            assert wb.state.edit(draft["draft_id"], "edit", 1, "编辑后的中文回复")["version"] == 2
            confirmation = await wb.prepare("prepare", draft["draft_id"], 2)
            assert confirmation["text"] == "编辑后的中文回复"
            assert await wb.prepare("prepare", draft["draft_id"], 2) == confirmation
            result = await wb.confirm("confirm", confirmation["confirmation_id"])
            assert await wb.confirm("confirm", confirmation["confirmation_id"]) == result
            await asyncio.gather(*wb.tasks)
            assert await wb.confirm("different-http-id", confirmation["confirmation_id"]) == result
            assert wb.sender.calls == 1 and wb.sender.log.count("keys") == 1
            assert ledger.action_summary(result["action_id"])["state"] == "UNCONFIRMED"
            assert wb.phone_confirmation(result["action_id"])["state"] == "CONFIRMED"
            assert wb.phone_confirmation(result["action_id"])["state"] == "CONFIRMED"
            assert ledger._db.execute("SELECT COUNT(*) FROM delivery_evidence WHERE action_id=?", (result["action_id"],)).fetchone()[0] == 2
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("damage", ["edit", "message", "persona", "pause", "expire", "history"])
def test_stale_confirmation_never_creates_action_or_consumes_input(tmp_path, damage):
    async def run():
        clock = [1_800_000_000.0]
        with Ledger(tmp_path / "real.db", now=lambda: clock[0]) as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            draft = await generate(wb, source, "合法正文")
            confirmation = await wb.prepare("prepare", draft["draft_id"], 1)
            counts = ledger.counts()
            if damage == "edit":
                wb.state.edit(draft["draft_id"], "edit", 1, "另一版")
            elif damage == "message":
                add_source(ledger, 2)
            elif damage == "persona":
                ledger.save_persona(1, "新名字", "风格", "")
            elif damage == "pause":
                ledger.pause(True)
            elif damage == "expire":
                clock[0] += 61
            else:
                ledger._db.execute("UPDATE wechat_send_operations SET restore_verified=0")
            with pytest.raises(SocialError):
                await wb.confirm("confirm", confirmation["confirmation_id"])
            assert ledger.counts() == counts and wb.sender.calls == 0
            assert ledger._db.execute("SELECT action_id FROM wechat_channel_reviews WHERE review_id=?", (confirmation["confirmation_id"],)).fetchone()[0] is None
            await wb.aclose()
    asyncio.run(run())


def test_payload_cursor_atomicity_duplicate_and_restart(tmp_path, monkeypatch):
    from wechat_agent.workbench import state as module

    path = tmp_path / "real.db"
    with Ledger(path) as ledger:
        source = seed(ledger)
        initial = ledger._db.execute("SELECT COUNT(*) FROM bridge_message_payloads").fetchone()[0]
        BridgeStore(ledger).ingest(BINDING, "selected", Cursor(0, 0), (source,))
        assert ledger._db.execute("SELECT COUNT(*) FROM bridge_message_payloads").fetchone()[0] == initial
        original = module.ingest_payload

        def fail(*args):
            original(*args)
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(module, "ingest_payload", fail)
        with pytest.raises(OSError):
            add_source(ledger, 2)
        assert BridgeStore(ledger).cursor(BINDING, "selected") == Cursor(1, 1)
        assert ledger._db.execute("SELECT COUNT(*) FROM bridge_message_payloads").fetchone()[0] == initial
    with Ledger(path) as ledger:
        assert ledger._db.execute("SELECT text FROM bridge_message_payloads WHERE message_id=?", (source.message_id,)).fetchone()[0] == source.text


def test_restart_keeps_unknown_generation_and_expires_confirmation(tmp_path):
    async def first():
        with Ledger(path) as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            draft = await generate(wb, source, "草稿")
            await wb.prepare("prepare", draft["draft_id"], 1)
            # Simulate a hard interruption during a model call.
            ledger._db.execute("UPDATE workbench_drafts SET state='RUNNING'")
            return draft["draft_id"]

    path = tmp_path / "real.db"
    did = asyncio.run(first())
    with Ledger(path) as ledger:
        assert ledger._db.execute("SELECT state FROM workbench_drafts WHERE draft_id=?", (did,)).fetchone()[0] == "UNKNOWN"
        assert ledger._db.execute("SELECT state FROM workbench_requests WHERE kind='prepare'").fetchone()[0] == "CANCELLED"


def test_schema8_backup_atomic_failure_and_future_rejection(tmp_path, monkeypatch):
    from wechat_agent.workbench import state as module

    path = tmp_path / "migration.db"
    with Ledger(path) as ledger:
        seed(ledger)
        for table in ("reader_private_targets", "workbench_requests", "workbench_draft_revisions", "workbench_drafts", "workbench_context", "bridge_message_payloads"):
            ledger._db.execute(f"DROP TABLE {table}")
        ledger._db.execute("PRAGMA user_version=8")
    original = module.migrate

    def fail(db):
        original(db)
        raise OSError("migration failure")

    monkeypatch.setattr(module, "migrate", fail)
    with pytest.raises(OSError), Ledger(path):
        pass
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_drafts'").fetchone()
    assert list(tmp_path.glob("*.v8-*.bak"))
    monkeypatch.setattr(module, "migrate", original)
    with Ledger(path) as ledger:
        assert ledger._db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] == 1
        ledger._db.execute("PRAGMA user_version=11")
    with pytest.raises(SocialError, match="unsupported_schema"), Ledger(path):
        pass


def test_real_http_routes_deny_sandbox_and_browser_evidence(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            seed(ledger)
            wb = workbench(ledger)
            server = WorkbenchConsoleServer(ledger, wb)
            for path, data in [("/api/dispatch", {"action_id": "x"}), ("/api/messages", {}),
                               ("/api/wechat/send/confirm", {"request_id": "x", "confirmation_id": "x", "approved": True})]:
                with pytest.raises(SocialError):
                    await server._route("POST", path, data)
            assert (await server._route("GET", "/api/wechat/state", None))["target"] == "filehelper"
            await wb.aclose()
    asyncio.run(run())


def test_inflight_generation_invalidated_by_new_message_and_cancelled_call_not_replayed(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []

            async def generator(*_):
                calls.append(1)
                entered.set()
                await release.wait()
                return Decision(ActionKind.REPLY, "迟到结果")

            wb = workbench(ledger, generator=generator)
            request = dict(request_id="inflight", source_message_id=source.message_id,
                           expected_message_revision=1, expected_persona_version=1)
            draft = await wb.generate(**request)
            await entered.wait()
            source2 = add_source(ledger, 2)
            wb.state.cancel(draft["draft_id"])
            with pytest.raises(SocialError, match="busy"):
                await wb.generate(request_id="second", source_message_id=source2.message_id,
                                  expected_message_revision=2, expected_persona_version=1)
            release.set()
            await asyncio.gather(*wb.tasks)
            assert wb.state.draft(draft["draft_id"])["text"] is None
            assert (await wb.generate(**request))["state"] == "CANCELLED" and calls == [1]
            await wb.aclose()
    asyncio.run(run())


def test_budget_counts_failed_calls_in_shanghai_day_and_silent_never_queues(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db", now=lambda: 1_800_000_000.0) as ledger:
            seed(ledger)
            wb = workbench(ledger)
            calls = []

            async def silent(*_):
                calls.append(1)
                return Decision(ActionKind.SILENT, "")

            wb.generator = silent
            for n in range(1, 31):
                source = add_source(ledger, n) if n > 1 else None
                mid = source.message_id if source else wb.snapshot()["messages"][0]["message_id"]
                draft = await wb.generate(request_id="budget-"+str(n), source_message_id=mid,
                    expected_message_revision=n, expected_persona_version=1)
                await asyncio.gather(*wb.tasks)
                assert wb.state.draft(draft["draft_id"])["state"] == "SILENT"
            source = add_source(ledger, 31)
            with pytest.raises(SocialError, match="model_daily_budget"):
                await wb.generate(request_id="budget-31", source_message_id=source.message_id,
                                  expected_message_revision=31, expected_persona_version=1)
            assert len(calls) == 30
            assert ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
            await wb.aclose()
    asyncio.run(run())


def test_live_reader_preserves_draft_mode_and_baseline_uses_fresh_tails(tmp_path):
    from tests.test_wechat_receiver import EXPECTED, WINDOW, worker
    from wechat_agent.bridges.wechat.receive_process import ReaderSession
    from wechat_agent.workbench.native import NativeWorkbenchSender

    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            session = ReaderSession(ledger, worker(), WINDOW, EXPECTED, mode="draft")
            await session.start(initialize=True)
            await session.poll()
            await session.poll()
            assert ledger._db.execute("SELECT mode FROM bridge_bindings WHERE active=1").fetchone()[0] == "draft"
            await session.close()
        calls = []

        async def rpc(command):
            calls.append(command)
            return dict(shards={"message_0.db": dict(identity="identity", tail=dict(sort_seq=99, local_id=20), local_fence=20)})

        async def poll():
            return dict(more=False)

        receiver = SimpleNamespace(binding=BINDING, poll=poll, rpc_lock=asyncio.Lock(), worker=SimpleNamespace(rpc=rpc),
            catalog=SimpleNamespace(checkpoints=lambda: {"message_0.db": dict(shard="message_0.db:identity", start=dict(sort_seq=1, local_id=1))}))
        sender = NativeWorkbenchSender(None, receiver, "f"*64, (None, None, None, None))
        assert await sender.baseline(BINDING) == {"message_0.db:identity": Cursor(99, 20)}
        assert calls == ["echo_baseline"]
    asyncio.run(run())


def test_actual_http_auth_origin_and_asset_delivery(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            seed(ledger)
            wb = workbench(ledger)
            server = WorkbenchConsoleServer(ledger, wb, token="synthetic-token")
            await server.start(0)
            try:
                async def fetch(path, *, token=None, origin=None, body=None):
                    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
                    headers = [f"{'POST' if body else 'GET'} {path} HTTP/1.1", f"Host: 127.0.0.1:{server.port}"]
                    if token:
                        headers.append("Authorization: Bearer " + token)
                    if origin:
                        headers.append("Origin: " + origin)
                    if body:
                        headers += ["Content-Type: application/json", "Content-Length: " + str(len(body))]
                    writer.write(("\r\n".join(headers)+"\r\n\r\n").encode()+(body or b""))
                    await writer.drain()
                    data = await reader.read()
                    writer.close()
                    await writer.wait_closed()
                    return data

                assert b"200 Response" in await fetch("/")
                assert b"/assets/workbench/entry.js" in await fetch("/")
                assert b"401 Response" in await fetch("/api/wechat/state")
                assert b"200 Response" in await fetch("/api/wechat/state", token="synthetic-token")
                assert b"403 Response" in await fetch("/api/wechat/disconnect", token="synthetic-token", origin="http://other.invalid", body=b"{}")
                assert wb.reading
            finally:
                await server.aclose()
                await wb.aclose()
    asyncio.run(run())


def test_new_self_echo_after_input_fences_submit_but_allows_recovery(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            draft = await generate(wb, source, "待提交的回复")
            confirmation = await wb.prepare("prepare", draft["draft_id"], 1)
            wb.sender.before_submit = lambda: add_source(ledger, 2)
            result = await wb.confirm("confirm", confirmation["confirmation_id"])
            await asyncio.gather(*wb.tasks)
            op = ledger._db.execute("SELECT * FROM wechat_send_operations WHERE action_id=?", (result["action_id"],)).fetchone()
            assert op["input_permitted"] == 1 and op["submit_permitted"] == 0
            assert op["worker_reaped"] == op["restore_verified"] == 1
            assert ledger.action_summary(result["action_id"])["state"] == "UNCONFIRMED"
            assert "keys" not in wb.sender.log
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("reaped,restored", [(True, True), (False, True), (True, False)])
def test_native_preparation_reaps_before_restoring_and_persists_failed_cleanup(tmp_path, monkeypatch, reaped, restored):
    from wechat_agent.bridges.wechat import background_lock
    from wechat_agent.workbench import native

    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            draft = await generate(wb, source, "准备检查正文")
            log = []

            class PreparationWorker:
                process = SimpleNamespace(pid=123)

                def __init__(self, *_):
                    pass

                async def start(self, *_):
                    log.append("worker_started")

                async def _rpc(self, _):
                    return dict(readiness={key: True for key in READY_FIELDS})

                async def stop(self):
                    log.append("worker_stopped")
                    return reaped

            class Lease:
                def __init__(self, memory, window, record):
                    self.record = record

                def open(self):
                    self.record("GATE_ENABLED", 0)

                def close(self, *, restore=True):
                    log.append("restore" if restore else "retain_gate")
                    self.record("GATE_RESTORED" if restore else "GATE_RETAINED", 0)
                    return restore and restored

            async def poll():
                return dict(more=False)

            monkeypatch.setattr(native, "BackgroundWorker", PreparationWorker)
            monkeypatch.setattr(native, "AccessibilityLease", Lease)
            monkeypatch.setattr(background_lock, "writer_locks", lambda _: SimpleNamespace(close=lambda: log.append("unlock")))
            receiver = SimpleNamespace(binding=BINDING, poll=poll)
            profile = SimpleNamespace(__file__=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts/wechat_42013_profile.py"))
            sender = native.NativeWorkbenchSender(ledger, receiver, "f" * 64,
                (SimpleNamespace(NativeMemory=lambda *_a, **_kw: None), None, profile, None))
            if reaped and restored:
                facts, checker = await sender.prepare(draft["draft_id"])
                assert all(facts.values()) and checker(ledger, BINDING)
                assert log == ["worker_started", "worker_stopped", "restore", "unlock"]
            else:
                with pytest.raises(SocialError, match="background_preflight_restore_failed"):
                    await sender.prepare(draft["draft_id"])
                assert sender.closed
                assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "workbench_preparation_cleanup_failed"
                if not reaped:
                    assert log == ["worker_started", "worker_stopped", "retain_gate"]
                    assert sender._held_preparation_locks is not None
                else:
                    assert log == ["worker_started", "worker_stopped", "restore", "unlock"]
            assert ledger._db.execute("SELECT COUNT(*) FROM workbench_requests WHERE kind='native_preparation'").fetchone()[0] == 1
            assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] == 1
            await wb.aclose()
    asyncio.run(run())


def test_two_prepared_drafts_cannot_consume_parallel_send_permits(tmp_path):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            first = seed(ledger)
            second = add_source(ledger, 2)
            wb = workbench(ledger)
            draft1 = await generate(wb, first, "第一个回复")
            draft2 = await wb.generate(request_id="second-generation", source_message_id=second.message_id,
                                      expected_message_revision=2, expected_persona_version=1)
            await asyncio.gather(*wb.tasks)
            one = await wb.prepare("prepare-one", draft1["draft_id"], 1)
            two = await wb.prepare("prepare-two", draft2["draft_id"], 1)
            await wb.confirm("confirm-one", one["confirmation_id"])
            with pytest.raises(SocialError):
                await wb.confirm("confirm-two", two["confirmation_id"])
            assert ledger._db.execute("SELECT COUNT(*) FROM workbench_drafts WHERE action_id IS NOT NULL").fetchone()[0] == 1
            await asyncio.gather(*wb.tasks)
            assert wb.sender.calls == 1
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["open", "session"])
def test_live_auth_failure_records_safe_stage_and_zero_inference_without_retry(tmp_path, phase):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            calls = []

            async def get_session():
                calls.append("session")
                raise ProviderError(ErrorCode.CONNECTION)

            async def catalog(_session):
                pytest.fail("authentication failure must not fetch the catalog")

            @asynccontextmanager
            async def application(*_args):
                calls.append("open")
                if phase == "open":
                    raise ProviderError(ErrorCode.AUTH)
                yield SimpleNamespace(tokens=SimpleNamespace(context_key=lambda: "epoch", get_session=get_session))

            wb.generator = LiveDraftGenerator(ledger, "test-model", Path("unused"),
                                              app_factory=application, catalog_fetch=catalog)
            request = dict(request_id="failed-live", source_message_id=source.message_id,
                           expected_message_revision=1, expected_persona_version=1)
            draft = await wb.generate(**request)
            await wb.generation_task
            result = wb.state.draft(draft["draft_id"])
            assert result["state"] == "FAILED"
            assert result["reason"] == ("authentication_required" if phase == "open" else "connection_failure")
            trace = result["model_trace"]
            assert trace["phase"] == phase and trace["inference_calls"] == trace["model_http_attempts"] == 0
            assert source.text not in json.dumps(trace) and "reply_text" not in trace
            assert (await wb.generate(**request))["draft_id"] == draft["draft_id"]
            assert calls == (["open"] if phase == "open" else ["open", "session"])
            assert wb.sender.calls == 0
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("model_failure", [False, True])
def test_live_generator_counts_actual_inference_and_preserves_safe_error(tmp_path, model_failure):
    async def run():
        with Ledger(tmp_path / "real.db") as ledger:
            source = seed(ledger)
            wb = workbench(ledger)
            provider = SimpleNamespace(http_attempts=0)

            async def session():
                return "synthetic-session"

            async def turn(scope, content, deliver):
                assert await deliver(None) is False
                provider.http_attempts += 1
                if model_failure:
                    raise ProviderError(ErrorCode.PROTOCOL)
                return SimpleNamespace(draft=SimpleNamespace(text=json.dumps(dict(action="REPLY", reply_text="合成回复"))))

            @asynccontextmanager
            async def application(*_args):
                yield SimpleNamespace(tokens=SimpleNamespace(context_key=lambda: "epoch", get_session=session),
                                      provider=provider, engine=SimpleNamespace(turn=turn))

            async def catalog(_):
                return SimpleNamespace(contains=lambda slug: slug == "test-model", context_key="epoch")

            wb.generator = LiveDraftGenerator(ledger, "test-model", Path("unused"),
                                              app_factory=application, catalog_fetch=catalog)
            request = dict(request_id="model-path", source_message_id=source.message_id,
                           expected_message_revision=1, expected_persona_version=1)
            draft = await wb.generate(**request)
            await wb.generation_task
            result = wb.state.draft(draft["draft_id"])
            assert result["state"] == ("FAILED" if model_failure else "READY")
            assert result["model_trace"]["inference_calls"] == result["model_trace"]["model_http_attempts"] == 1
            assert result["model_trace"]["phase"] == ("model" if model_failure else "complete")
            if model_failure:
                assert result["reason"] == "incompatible_protocol" and result["text"] is None
            else:
                assert result["text"] == "合成回复"
            assert "合成回复" not in json.dumps(result["model_trace"])
            await wb.generate(**request)
            assert provider.http_attempts == 1
            await wb.aclose()
    asyncio.run(run())
