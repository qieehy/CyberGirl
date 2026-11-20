"""Key H4 acceptance scenarios; real subscription requests are never pytest fixtures."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from tests.auth_support import ByteStream, reply
from tests.support import LocalServer, frame, terminal
from wechat_agent.bootstrap import open_mock_application
from wechat_agent.providers.codex import (
    CodexEndpoint,
    CodexSession,
    CodexTextProvider,
    ErrorCode,
    ProviderError,
    SyntheticSession,
)
from wechat_agent.providers.codex.auth import CodexAuthClient
from wechat_agent.providers.codex.catalog import (
    MAX_CATALOG_BYTES,
    CatalogModel,
    ModelCatalog,
    fetch_catalog,
)
from wechat_agent.providers.codex.network import application_proxy
from wechat_agent.verification import run_probe, verify_command

SCRATCH = Path(__file__).resolve().parents[1] / ".build" / "h4-tests"


class H4AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_saved_proxy_survives_absent_environment_and_does_not_escape_test_routes(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            root = Path(temporary)
            config = root / "wechat-agent.local.toml"
            config.write_text('[network]\nproxy = "http://127.0.0.1:12334"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(application_proxy(local_testing=False), "http://127.0.0.1:12334")
                with patch.dict(os.environ, {"WECHAT_AGENT_PROXY": "http://127.0.0.1:7897"}):
                    self.assertEqual(application_proxy(local_testing=False), "http://127.0.0.1:7897")
                config.write_text('[network]\nproxy = "https://unrelated.invalid"', encoding="utf-8")
                with self.assertRaises(ProviderError):
                    application_proxy(local_testing=False)
                self.assertIsNone(application_proxy(local_testing=True))
                self.assertIsNone(application_proxy(local_testing=False, injected_transport=True))

    async def test_missing_content_type_requires_a_valid_completed_sse_stream(self):
        async def session():
            return SyntheticSession()

        metadata = {"type": "response.metadata", "response_id": "resp_test", "metadata": {}}
        cases = [(b"", frame(metadata) + frame(terminal()), True),
                 (b"", frame(metadata), False),
                 (b"", frame(dict(metadata, response_id="resp_wrong")) + frame(terminal()), False),
                 (b"", b"<html>upstream error</html>\n\n", False),
                 (b"Content-Type: application/json\r\n", frame(terminal()), False)]
        for extra, body, success in cases:
            async def handle(request, reader, writer, extra=extra, body=body):
                writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                             b"X-OAI-Request-ID: req_h4\r\n" + extra + b"\r\n" + body)
                await writer.drain()

            async with LocalServer(handle) as server:
                async with CodexTextProvider(model="synthetic-model", session_source=session,
                        endpoint=CodexEndpoint(server.base_url, local_testing=True)) as provider:
                    messages = [{"role": "system", "content": "Test"}, {"role": "user", "content": "Hi"}]
                    if success:
                        result = await provider(messages, [])
                        self.assertEqual(result.message.content, "连接验证成功")
                        self.assertEqual(result.request_id, "req_h4")
                    else:
                        with self.assertRaises(ProviderError):
                            await provider(messages, [])
                    self.assertEqual(provider.http_attempts, 1)

    async def test_account_catalog_keeps_subscription_models_and_binds_headers(self):
        requests = []

        async def handle(request):
            requests.append(request)
            return reply({"models": [
                {"slug": "synthetic-visible", "priority": 2, "supported_in_api": False},
                {"slug": "synthetic-first", "priority": 1},
                {"slug": "synthetic-hidden", "priority": 0, "visibility": "hidden"},
            ]}, headers={"X-Request-ID": "req_catalog"})

        session = CodexSession("h4-fake-access", "h4-fake-account", "eu", "h4-epoch")
        result = await fetch_catalog(session, transport=httpx.MockTransport(handle))
        self.assertEqual([m.slug for m in result.models], ["synthetic-first", "synthetic-visible"])
        self.assertEqual(result.context_key, "h4-epoch")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual((request.method, request.url.host, request.url.path),
                         ("GET", "chatgpt.com", "/backend-api/codex/models"))
        self.assertEqual(request.url.params["client_version"], "0.164.0")
        self.assertEqual(request.headers["authorization"], "Bearer h4-fake-access")
        self.assertEqual(request.headers["chatgpt-account-id"], "h4-fake-account")
        self.assertEqual(request.headers["x-openai-internal-codex-residency"], "eu")
        self.assertNotIn("h4-fake", json.dumps(result.summary()))
        self.assertNotIn("h4-epoch", json.dumps(result.summary()))
        with self.assertRaises(ProviderError):
            await fetch_catalog(session, endpoint=CodexEndpoint("http://127.0.0.1:8543", local_testing=True),
                                transport=httpx.MockTransport(handle))
        self.assertEqual(len(requests), 1)

    async def test_catalog_failures_do_not_redirect_retry_or_use_a_fallback_and_region_is_clear(self):
        requests = []
        stream = ByteStream(b"x" * (MAX_CATALOG_BYTES + 1))

        async def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return reply(status=302, headers={"Location": "https://unrelated.invalid/models"})
            return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)

        session = CodexSession("h4-fake-access", "h4-fake-account")
        with self.assertRaises(ProviderError) as redirected:
            await fetch_catalog(session, transport=httpx.MockTransport(handle))
        self.assertEqual((redirected.exception.status, len(requests)), (302, 1))
        with self.assertRaises(ProviderError) as oversized:
            await fetch_catalog(session, transport=httpx.MockTransport(handle))
        self.assertEqual((oversized.exception.code, len(requests)), (ErrorCode.LIMIT, 2))
        self.assertTrue(stream.closed)

        async def region(request):
            return reply({"error": {"code": "unsupported_country_region_territory",
                                    "message": "H4-SENSITIVE-REMOTE-TEXT"}}, status=403)

        auth = CodexAuthClient(transport=httpx.MockTransport(region))
        try:
            with self.assertRaises(ProviderError) as blocked:
                await auth.begin_device_login()
            self.assertEqual((blocked.exception.code, blocked.exception.status), (ErrorCode.REGION, 403))
            self.assertNotIn("H4-SENSITIVE", str(blocked.exception))
        finally:
            await auth.aclose()

    async def test_probe_gates_inference_uses_runtime_and_never_records_text_or_replays_evidence(self):
        opened = []

        def factory(model, directory, settings):
            context = open_mock_application(settings, scratch=SCRATCH)
            return context

        async def catalog(session):
            return ModelCatalog((CatalogModel("h3-mock-model", 1),), "synthetic-time", "req_catalog", session.context_key)

        with patch("wechat_agent.verification.fetch_catalog", side_effect=catalog):
            for stage in ("provider", "runtime", "restart"):
                result = await run_probe(stage, "h3-mock-model", SCRATCH, app_factory=factory)
                opened.append(result)
                self.assertEqual(result["model_http_attempts"], 1)
                self.assertEqual(result["logical_model_calls"], 1)
                self.assertNotIn("模拟回复", json.dumps(result, ensure_ascii=False))
                self.assertNotIn("h1-synthetic-token", json.dumps(result))
            self.assertEqual(opened[0]["status"], "passed")
            # The echo backend cannot adopt a persona. Keep this failed criterion
            # visible instead of treating a completed HTTP response as H4 success.
            self.assertEqual(opened[1]["status"], "acceptance_failed")
            self.assertFalse(opened[1]["checks"]["expected_marker"])
            self.assertEqual(opened[1]["runtime"]["class"], "runtime.runtime.Runtime")
            self.assertEqual(opened[2]["status"], "passed")
            with patch("wechat_agent.providers.codex.CodexTextProvider.__call__",
                       side_effect=AssertionError("uncatalogued model called")):
                with self.assertRaises(ProviderError) as absent:
                    await run_probe("provider", "not-in-catalog", SCRATCH, app_factory=factory)
                self.assertEqual(absent.exception.code, ErrorCode.INPUT)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            output = Path(temporary) / "probe.json"
            probe = AsyncMock(return_value=opened[0])
            with patch("wechat_agent.verification.run_probe", probe):
                await verify_command("provider", "h3-mock-model", SCRATCH, output)
                with self.assertRaises(ProviderError):
                    await verify_command("provider", "h3-mock-model", SCRATCH, output)
                probe.assert_awaited_once()
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "passed")
            self.assertNotIn("content", saved)
