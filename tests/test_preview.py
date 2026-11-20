"""Three integrated acceptance scenarios for explicitly triggered persona previews."""

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
from llm.models import AssistantMessage, LLMResult

from wechat_agent.chat import ChatEngine
from wechat_agent.console import SCOPE, ConsoleServer
from wechat_agent.preview import PreviewService
from wechat_agent.providers.codex import ErrorCode, ProviderError
from wechat_agent.social.decision import RuntimeDecider, SyntheticModel
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.social.sandbox import FakeAdapter
from wechat_agent.social.service import SocialService


class Fixture:
    def __init__(self):
        self.calls = 0
        self.catalog_calls = 0
        self.opens = self.closes = 0
        self.messages = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = None

    async def catalog(self, session):
        self.catalog_calls += 1
        return SimpleNamespace(context_key="fixture-epoch", contains=lambda slug: slug == "fixture-model")

    @asynccontextmanager
    async def application(self, model, directory, settings):
        self.opens += 1
        provider = SimpleNamespace(http_attempts=0)

        async def get_session():
            return object()

        async def call(messages, tools, **kwargs):
            self.calls += 1
            provider.http_attempts += 1
            self.messages = messages
            assert not tools
            self.entered.set()
            await self.release.wait()
            if self.failure:
                raise self.failure
            return LLMResult(AssistantMessage(content=json.dumps({"action": "REPLY", "reply_text": "你好，我是小禾。"})))

        try:
            async with ChatEngine(call, lambda: "fixture-epoch", settings) as engine:
                yield SimpleNamespace(engine=engine, provider=provider,
                                      tokens=SimpleNamespace(context_key=lambda: "fixture-epoch", get_session=get_session))
        finally:
            self.closes += 1


class PreviewAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".build" / "preview-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "ledger.sqlite3"
        self.fixture = Fixture()

    def preview(self, ledger):
        return PreviewService(ledger, "fixture-model", self.path.parent,
                              app_factory=self.fixture.application, catalog_fetch=self.fixture.catalog)

    async def test_authenticated_http_opt_in_preview_duplicate_and_restart_do_not_send(self):
        with Ledger(self.path) as ledger:
            ledger.configure_owner(SCOPE, "owner")
            before = ledger.counts()
            adapter = FakeAdapter()
            async with SocialService(ledger, RuntimeDecider(SyntheticModel()), [adapter]) as service:
                server = ConsoleServer(ledger, service, token="fixture")
                await server.start(0)
                try:
                    async with httpx.AsyncClient(base_url=server.origin, trust_env=False) as client:
                        payload = {"request_id": "http-preview", "expected_version": 1, "text": "介绍自己"}
                        self.assertEqual((await client.post("/api/preview", json=payload)).status_code, 403)
                        client.headers.update({"Origin": server.origin, "Authorization": "Bearer fixture"})
                        self.assertEqual((await client.post("/api/preview", json=payload)).json()["error"], "live_preview_disabled")
                        server.preview = self.preview(ledger)
                        await client.get("/api/state")
                        self.assertEqual(self.fixture.opens, 0)
                        result = (await client.post("/api/preview", json=payload)).json()
                        self.assertEqual(result["state"], "COMPLETED")
                        self.assertEqual(result["runtime_class"], "runtime.runtime.Runtime")
                        duplicate = (await client.post("/api/preview", json=payload)).json()
                        self.assertEqual(result, duplicate)
                        self.assertEqual((await client.post("/api/preview", json=dict(payload, text="changed"))).status_code, 409)
                        self.assertEqual(ledger.counts(), before)
                        self.assertEqual(len(adapter.sent), 0)
                finally:
                    await server.aclose()
        with Ledger(self.path) as reopened:
            self.assertEqual((await self.preview(reopened).run("http-preview", 1, "介绍自己"))["state"], "COMPLETED")
        self.assertEqual((self.fixture.calls, self.fixture.catalog_calls, self.fixture.opens, self.fixture.closes), (1, 1, 1, 1))
        self.assertEqual(json.loads(self.fixture.messages[-1]["content"])["confirmed_history"], [])

    async def test_version_and_pause_fence_inflight_preview_and_no_parallel_calls(self):
        with Ledger(self.path) as ledger:
            preview = self.preview(ledger)
            self.fixture.release.clear()
            task = asyncio.create_task(preview.run("inflight", 1, "介绍自己"))
            await asyncio.wait_for(self.fixture.entered.wait(), 3)
            self.assertEqual((await preview.run("inflight", 1, "介绍自己"))["state"], "RUNNING")
            with self.assertRaises(SocialError):
                await preview.run("second", 1, "介绍自己")
            ledger.save_persona(1, "新名字", "简洁", "")
            self.fixture.release.set()
            result = await task
            self.assertEqual(result["state"], "SUPERSEDED")
            self.assertNotIn("reply_text", result)
            ledger.pause(True)
            with self.assertRaises(SocialError):
                await preview.run("paused", 2, "介绍自己")
            self.assertEqual(self.fixture.calls, 1)

    async def test_failure_cancel_and_crash_unknown_are_durable_and_never_replayed(self):
        with Ledger(self.path) as ledger:
            preview = self.preview(ledger)
            self.fixture.failure = ProviderError(ErrorCode.CONNECTION)
            failed = await preview.run("failure", 1, "介绍自己")
            self.assertEqual((failed["state"], failed["model_http_attempts"]), ("FAILED", 1))
            self.assertEqual(await preview.run("failure", 1, "介绍自己"), failed)
            self.fixture.failure = None
            self.fixture.release.clear()
            self.fixture.entered.clear()
            task = asyncio.create_task(preview.run("cancel", 1, "介绍自己"))
            await asyncio.wait_for(self.fixture.entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await preview.run("cancel", 1, "介绍自己"))["state"], "UNKNOWN")
            # A RUNNING row is what a hard process exit leaves behind.
            with ledger._transaction() as db:
                db.execute("UPDATE previews SET state='RUNNING' WHERE request_id='cancel'")
        with Ledger(self.path) as reopened:
            result = await self.preview(reopened).run("cancel", 1, "介绍自己")
            self.assertEqual(result["state"], "UNKNOWN")
            self.assertIsNone(result["model_http_attempts"])
        self.assertEqual((self.fixture.calls, self.fixture.opens, self.fixture.closes), (2, 2, 2))
