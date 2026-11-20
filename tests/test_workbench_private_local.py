"""Private local composition through real ledger/HTTP; no native or provider adapters."""

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from tests.test_wechat_background_control import BINDING
from wechat_agent.bridges.wechat.contracts import Cursor, SourceRecord
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.service import WeChatWorkbenchService

PEER = replace(BINDING, username="wxid_local_fixture", evidence_ref="fixture-only-private")


def service(ledger, *, enabled=True):
    store = BridgeStore(ledger)
    store.bind(PEER)
    store.baseline(PEER, "private-fixture", Cursor(0, 0))

    async def close():
        store.unbind(PEER)

    receiver = SimpleNamespace(binding=PEER, read_only_private=True, close=close,
                               catalog=SimpleNamespace(selection={"operator_label": "Amadues fixture"}))
    wb = WeChatWorkbenchService(ledger, receiver, local_drafts=enabled)
    wb.reading = True
    return wb


def incoming(ledger, *, binding=PEER, n=1, age=0, sender=None, kind="text"):
    source = SourceRecord(binding.account.account_id, binding.account.db_epoch, binding.username, "dm",
                          "private-fixture", n, n, sender or binding.username,
                          ledger.now() - age, ledger.now(), "fixture source", message_type=kind)
    store = BridgeStore(ledger)
    store.ingest(binding, source.shard, Cursor(n-1, n-1), (source,))
    return source


def payload(wb, source, text="本地草稿"):
    return dict(request_id="local-create", source_message_id=source.message_id,
                expected_message_revision=wb.state.revision(), expected_persona_version=1, text=text)


def protected(ledger):
    return {table: [tuple(r) for r in ledger._db.execute("SELECT * FROM " + table)]
            for table in ("outbox", "decisions", "wechat_send_control", "wechat_send_operations",
                          "wechat_send_journal", "wechat_channel_reviews", "delivery_evidence")}


def test_local_compose_edit_preview_is_idempotent_and_never_creates_a_send(tmp_path):
    async def run():
        with Ledger(tmp_path / "local.db") as ledger:
            wb = service(ledger)
            source = incoming(ledger)
            before = protected(ledger)
            data = payload(wb, source)
            first = await wb.compose(**data)
            assert (await wb.compose(**data))["draft_id"] == first["draft_id"]
            assert first["model"] == "local-operator" and first["model_calls"] == 0
            edited = wb.state.edit(first["draft_id"], "edit", 1, "核对后的正文")
            preview = wb.preview(draft_id=first["draft_id"], expected_version=2)
            assert preview["text"] == edited["text"] == "核对后的正文"
            assert preview["content_sha256"] == hashlib.sha256(edited["text"].encode()).hexdigest()
            assert preview["preview_only"] is True and preview["send_enabled"] is False
            assert "confirmation_id" not in preview
            assert preview["generation"] == PEER.generation and preview["target"] == PEER.username
            assert protected(ledger) == before and not wb.tasks
            assert ledger._db.execute("SELECT COUNT(*) FROM workbench_requests WHERE kind='model_trace'").fetchone()[0] == 0
            snap = wb.snapshot()
            assert snap["local_drafts_enabled"] and not snap["send_enabled"] and snap["model"] is None
            assert snap["target_label"] == "Amadues fixture"
            with pytest.raises(SocialError, match="identity_conflict"):
                await wb.compose(**{**data, "text": "same id changed text"})
            with pytest.raises(SocialError, match="already_used"):
                await wb.compose(**{**data, "request_id": "different"})
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("damage", ["self_echo", "stranger", "nontext", "other_scope", "unverified"])
def test_private_local_draft_rejects_unqualified_source(tmp_path, damage):
    async def run():
        clock = [1_800_000_000.0]
        with Ledger(tmp_path / "source.db", now=lambda: clock[0]) as ledger:
            wb = service(ledger)
            other = replace(PEER, username="wxid_other")
            if damage == "other_scope":
                BridgeStore(ledger).bind(other)
                BridgeStore(ledger).baseline(other, "private-fixture", Cursor(0, 0))
            source = incoming(ledger, binding=other if damage == "other_scope" else PEER,
                              age=121 if damage == "stale_ingest" else 0,
                              sender=PEER.account.self_id if damage == "self_echo" else "wxid_stranger" if damage == "stranger" else None,
                              kind="image" if damage == "nontext" else "text")
            if damage == "aged_after_ingest":
                clock[0] += 121
            if damage == "unverified":
                ledger._db.execute("UPDATE inbox SET source_verified=0")
            before = protected(ledger)
            with pytest.raises(SocialError, match="peer_source_required"):
                await wb.compose(**payload(wb, source))
            assert not ledger._db.execute("SELECT 1 FROM workbench_drafts").fetchone()
            assert protected(ledger) == before
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("history", ["stale_ingest", "aged_after_ingest", "previous_process"])
def test_existing_peer_message_can_be_selected_without_changing_source_facts(tmp_path, history):
    async def run():
        clock = [1_800_000_000.0]
        with Ledger(tmp_path / "existing.db", now=lambda: clock[0]) as ledger:
            wb = service(ledger)
            source = incoming(ledger, age=3600 if history == "stale_ingest" else 0)
            if history == "aged_after_ingest":
                clock[0] += 86400
            if history == "previous_process":
                ledger._db.execute("UPDATE inbox SET bridge_generation='previous-process'")
            facts = [tuple(r) for r in ledger._db.execute("SELECT * FROM bridge_records")]
            before = protected(ledger)
            draft = await wb.compose(**payload(wb, source))
            preview = wb.preview(draft_id=draft["draft_id"], expected_version=1)
            assert preview["source_occurred_at"] == source.occurred_at
            assert preview["source_disposition"] == ("stale_event" if history == "stale_ingest" else "OBSERVED")
            assert preview["send_enabled"] is False and draft["model_calls"] == 0
            assert [tuple(r) for r in ledger._db.execute("SELECT * FROM bridge_records")] == facts
            assert protected(ledger) == before
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("damage", ["edit", "message", "persona", "pause", "unbind", "expire"])
def test_saved_private_preview_revalidates_every_time(tmp_path, damage):
    async def run():
        clock = [1_800_000_000.0]
        with Ledger(tmp_path / "preview.db", now=lambda: clock[0]) as ledger:
            wb = service(ledger)
            draft = await wb.compose(**payload(wb, incoming(ledger)))
            did = draft["draft_id"]
            wb.preview(draft_id=did, expected_version=1)
            if damage == "edit":
                wb.state.edit(did, "edit", 1, "different saved revision")
            elif damage == "message":
                incoming(ledger, n=2)
            elif damage == "persona":
                ledger.save_persona(1, "changed", "style", "")
            elif damage == "pause":
                ledger.pause(True)
            elif damage == "unbind":
                BridgeStore(ledger).unbind(PEER)
            else:
                clock[0] += 120
            with pytest.raises(SocialError, match="version_conflict|stale_draft"):
                wb.preview(draft_id=did, expected_version=1)
            assert ledger._db.execute("SELECT expires_at FROM workbench_drafts").fetchone()[0] == 1_800_000_120
    asyncio.run(run())


@pytest.mark.parametrize("text", ["emoji🙂", "line\nbreak", "x" * 97])
def test_local_text_preserved_for_edit_but_not_previewed_as_sendable(tmp_path, text):
    async def run():
        with Ledger(tmp_path / "text.db") as ledger:
            wb = service(ledger)
            draft = await wb.compose(**payload(wb, incoming(ledger), text))
            assert draft["text"] == text and draft["send_error"]
            with pytest.raises(SocialError, match="background_"):
                wb.preview(draft_id=draft["draft_id"], expected_version=1)
            await wb.aclose()
    asyncio.run(run())


def test_capability_is_default_off_and_native_effects_remain_denied_even_if_injected(tmp_path):
    async def run():
        with Ledger(tmp_path / "off.db") as ledger:
            wb = service(ledger, enabled=False)
            with pytest.raises(SocialError, match="private_local_draft_scope_required"):
                await wb.compose(**payload(wb, incoming(ledger)))
            wb.generator = wb.model = wb.sender = object()
            for operation in (lambda: wb.generate(), lambda: wb.prepare("p", "d", 1), lambda: wb.confirm("c", "r")):
                with pytest.raises(SocialError, match="private_readonly_scope_required"):
                    await operation()
            assert not wb.snapshot()["send_enabled"] and wb.snapshot()["model"] is None
            wb.sender = None
            await wb.aclose()
    asyncio.run(run())


def test_restart_cancels_local_draft_without_replay_or_schema_change(tmp_path):
    path = tmp_path / "restart.db"

    async def create():
        with Ledger(path) as ledger:
            wb = service(ledger)
            draft = await wb.compose(**payload(wb, incoming(ledger)))
            return draft["draft_id"]

    did = asyncio.run(create())
    with Ledger(path) as ledger:
        wb = service(ledger)
        assert wb.state.draft(did)["state"] == "CANCELLED"
        assert ledger._db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert not ledger._db.execute("SELECT 1 FROM outbox").fetchone()


def test_authenticated_http_local_route_and_preview_cannot_confirm(tmp_path):
    async def run():
        with Ledger(tmp_path / "http.db") as ledger:
            wb = service(ledger)
            source = incoming(ledger)
            server = WorkbenchConsoleServer(ledger, wb, token="fixture-token")
            await server.start(0)
            try:
                async with httpx.AsyncClient(base_url=server.origin, trust_env=False, timeout=5) as client:
                    data = payload(wb, source)
                    url = "/api/wechat/local-drafts"
                    assert (await client.post(url, json=data, headers={"Origin": server.origin})).status_code == 401
                    headers = {"Origin": server.origin, "Authorization": "Bearer fixture-token"}
                    assert (await client.post(url, json=data, headers={**headers, "Origin": "http://other.invalid"})).status_code == 403
                    assert (await client.post(url, json={**data, "target": "spoof"}, headers=headers)).status_code == 400
                    response = await client.post(url, json=data, headers=headers)
                    assert response.status_code == 200, response.text
                    did = response.json()["draft_id"]
                    result = await client.post(url + "/preview", json={"draft_id": did, "expected_version": 1}, headers=headers)
                    assert result.status_code == 200 and result.json()["preview_only"] is True
                    for route, body in (("/api/wechat/send/prepare", dict(request_id="prepare", draft_id=did, expected_version=1)),
                                        ("/api/wechat/send/confirm", dict(request_id="confirm", confirmation_id=did)),
                                        ("/api/wechat/drafts", {k:v for k,v in data.items() if k != "text"})):
                        denied = await client.post(route, json=body, headers=headers)
                        assert denied.status_code == 400 and denied.json()["error"] == "private_readonly_scope_required"
                    assert not ledger._db.execute("SELECT 1 FROM outbox").fetchone()
            finally:
                await server.aclose()
                await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("extra", [["--review-unknown-action", "old"], ["--uncertainty-review-reference", "ref"],
                                   ["--wechat-account-evidence", "old.json"]])
def test_private_cli_denies_effect_options_before_opening_receiver(monkeypatch, extra):
    from wechat_agent.cli import main

    monkeypatch.setattr("sys.argv", ["wechat-agent", "console", "--mode", "wechat-private", *extra])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2


@pytest.mark.parametrize("mode", ["sandbox", "wechat"])
def test_local_draft_option_requires_private_mode(monkeypatch, mode):
    from wechat_agent.cli import main

    monkeypatch.setattr("sys.argv", ["wechat-agent", "console", "--mode", mode, "--local-drafts"])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_private_cli_reuses_original_ledger_and_constructs_no_effect_options(tmp_path, monkeypatch, enabled):
    from wechat_agent.cli import main

    original = tmp_path / ".state/social/wechat-sender-poc.sqlite3"
    original.parent.mkdir(parents=True)
    original.touch()
    calls = []

    async def launch(database, port, **kwargs):
        calls.append((database, port, kwargs))

    monkeypatch.setattr("wechat_agent.workbench.native.native_source_root", lambda: tmp_path)
    monkeypatch.setattr("wechat_agent.workbench.console.run_private_workbench_console", launch)
    monkeypatch.setattr("sys.argv", ["wechat-agent", "console", "--mode", "wechat-private",
                                    "--wechat-private-scope", "selected.json", "--wechat-pid", "1",
                                    "--wechat-hwnd", "2", "--wechat-created-filetime", "3",
                                    *(["--local-drafts"] if enabled else [])])
    main()
    assert calls == [(original, 8765, {"local_drafts": enabled, "native_options": {
        "scope_file": __import__("pathlib").Path("selected.json"), "pid": 1, "hwnd": 2, "created_filetime": 3}})]


def test_private_console_port_failure_reaps_receiver(tmp_path, monkeypatch):
    from wechat_agent.workbench.console import run_private_workbench_console

    closed = []

    async def close():
        closed.append(True)

    async def receiver(ledger, **options):
        BridgeStore(ledger).bind(PEER)
        return SimpleNamespace(binding=PEER, read_only_private=True, close=close)

    async def fail_start(self, port):
        raise SocialError("console_port_unavailable")

    monkeypatch.setattr("wechat_agent.workbench.private_native.open_private_receiver", receiver)
    monkeypatch.setattr(WorkbenchConsoleServer, "start", fail_start)
    with pytest.raises(SocialError, match="console_port_unavailable"):
        asyncio.run(run_private_workbench_console(tmp_path / "failed.db", 8765, local_drafts=True, native_options={}))
    assert closed == [True]


def test_close_then_rebind_in_same_ledger_cannot_revive_local_preview(tmp_path):
    async def run():
        with Ledger(tmp_path / "close.db") as ledger:
            wb = service(ledger)
            draft = await wb.compose(**payload(wb, incoming(ledger)))
            await wb.aclose()
            resumed = service(ledger)
            assert resumed.state.draft(draft["draft_id"])["reason"] == "workbench_closed"
            with pytest.raises(SocialError, match="stale_draft"):
                resumed.preview(draft_id=draft["draft_id"], expected_version=1)
            await resumed.aclose()
    asyncio.run(run())


def test_local_close_storage_failure_still_closes_receiver(tmp_path, monkeypatch):
    async def run():
        with Ledger(tmp_path / "close-fault.db") as ledger:
            wb = service(ledger)
            closed = []

            async def close():
                closed.append(True)

            def storage_failure():
                raise SocialError("synthetic_close_storage_failure")

            wb.receiver.close = close
            monkeypatch.setattr(ledger, "_transaction", storage_failure)
            with pytest.raises(SocialError, match="synthetic_close_storage_failure"):
                await wb.aclose()
            assert closed == [True]
    asyncio.run(run())
