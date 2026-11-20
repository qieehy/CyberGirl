import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from tests.auth_support import ByteStream, FakeClock, ignore_challenge, reply, token_payload
from tests.support import LocalServer, Request, frame, terminal
from wechat_agent.providers.codex import (
    CodexEndpoint,
    CodexSession,
    CodexTextProvider,
    ErrorCode,
    ProviderError,
)
from wechat_agent.providers.codex.auth import CodexAuthClient
from wechat_agent.providers.codex.auth_models import AuthEndpoint, parse_tokens
from wechat_agent.providers.codex.credentials import CredentialStore
from wechat_agent.providers.codex.token_manager import TokenManager

SCRATCH = Path(__file__).resolve().parents[1] / ".build" / "h2-tests"


class AuthHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.endpoint = AuthEndpoint("http://127.0.0.1:8543", local_testing=True)
        self.clients = []

    def client(self, handler, **kwargs):
        client = CodexAuthClient(self.endpoint, now=lambda: self.clock.now,
                                 monotonic=lambda: self.clock.monotonic, sleep=self.clock.sleep,
                                 transport=httpx.MockTransport(handler), **kwargs)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()

    async def test_device_waiting_exchange_and_claim_routing(self):
        requests = []
        polls = 0

        def handler(request):
            nonlocal polls
            requests.append(request)
            if request.url.path.endswith("/usercode"):
                return reply({"user_code": "H2-TEST", "device_auth_id": "h2-device", "interval": "1"})
            if request.url.path.endswith("/deviceauth/token"):
                polls += 1
                if polls < 3:
                    return reply(status=403 if polls == 1 else 404)
                return reply({"authorization_code": "h2-auth-code", "code_verifier": "v" * 43})
            form = parse_qs(request.content.decode())
            self.assertEqual(form["grant_type"], ["authorization_code"])
            self.assertEqual(form["client_id"], [self.endpoint.binding.client_id])
            self.assertEqual(form["redirect_uri"], [self.endpoint.issuer + "/deviceauth/callback"])
            self.assertEqual(form["code_verifier"], ["v" * 43])
            return reply(token_payload(self.clock, residency="eu"))

        client = self.client(handler)
        challenge = await client.begin_device_login()
        result = await client.complete_device_login(challenge)
        self.assertEqual(self.clock.sleeps, [3, 3, 3])
        self.assertEqual(len(requests), 5)
        self.assertEqual(result.account_id, "h2-synthetic-account")
        self.assertEqual(result.residency, "eu")
        self.assertNotIn("H2-TEST", repr(challenge))
        self.assertNotIn("h2-device", repr(challenge))
        self.assertNotIn(result.refresh_token, repr(result))

    async def test_refresh_rotation_missing_new_refresh_and_binding_guard(self):
        requests = []
        payload = token_payload(self.clock)

        def handler(request):
            requests.append(request)
            self.assertEqual(parse_qs(request.content.decode())["refresh_token"], [previous.refresh_token])
            return reply(payload)

        client = self.client(handler)
        previous = parse_tokens(token_payload(self.clock, refresh="h2-old-refresh"), client.binding, self.clock.now)
        result = await client.refresh(previous)
        self.assertEqual(result.refresh_token, "h2-synthetic-refresh-new")
        payload.pop("refresh_token")
        retained = await client.refresh(previous)
        self.assertEqual(retained.refresh_token, "h2-old-refresh")
        live = parse_tokens(token_payload(self.clock), AuthEndpoint().binding, self.clock.now)
        with self.assertRaises(ProviderError):
            await client.refresh(live)
        self.assertEqual(len(requests), 2)

    async def test_poll_expiry_is_bounded_and_initial_403_is_not_pending(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/usercode"):
                return reply({"user_code": "H2-TEST", "device_auth_id": "device", "interval": 3, "expires_in": 5})
            return reply(status=403)

        client = self.client(handler)
        challenge = await client.begin_device_login()
        with self.assertRaises(ProviderError) as caught:
            await client.complete_device_login(challenge)
        self.assertEqual(caught.exception.code, ErrorCode.DEVICE_EXPIRED)
        self.assertEqual(len(calls), 2)
        initial = self.client(lambda request: reply(status=403))
        with self.assertRaises(ProviderError) as caught:
            await initial.begin_device_login()
        self.assertEqual(caught.exception.code, ErrorCode.FORBIDDEN)

    async def test_rate_limit_and_terminal_http_errors_do_not_retry(self):
        for status, code in [(429, ErrorCode.RATE_LIMIT), (401, ErrorCode.AUTH),
                             (400, ErrorCode.AUTH), (403, ErrorCode.FORBIDDEN), (503, ErrorCode.HTTP)]:
            calls = []

            def handler(request, status=status, calls=calls):
                calls.append(request)
                return reply({"error": "do-not-log-h2-secret"}, status=status, headers={"Retry-After": "13"})

            client = self.client(handler)
            previous = parse_tokens(token_payload(self.clock), client.binding, self.clock.now)
            with self.subTest(status=status), self.assertRaises(ProviderError) as caught:
                await client.refresh(previous)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.request_id, "req_auth")
            self.assertEqual(caught.exception.retry_after, 13 if status == 429 else None)
            self.assertNotIn("do-not-log-h2-secret", str(caught.exception))
            self.assertEqual(len(calls), 1)

    async def test_bad_token_and_json_shapes_are_safe_failures(self):
        valid = token_payload(self.clock)
        cases = [{}, {**valid, "access_token": "not-a-jwt"}, {**valid, "refresh_token": ""},
                 {**valid, "token_type": None}, {**valid, "token_type": "not-bearer"},
                 {**valid, "expires_in": True}, token_payload(self.clock, expires=-1),
                 token_payload(self.clock, account="invalid account")]
        for payload in cases:
            client = self.client(lambda request, payload=payload: reply(payload))
            previous = parse_tokens(valid, client.binding, self.clock.now)
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                await client.refresh(previous)
        for body in [b"not json", b"[]", b'{{', b'{"access_token":"a","access_token":"b"}', b"\xff"]:
            client = self.client(lambda request, body=body: reply(raw=body))
            previous = parse_tokens(valid, client.binding, self.clock.now)
            with self.subTest(body=body), self.assertRaises(ProviderError):
                await client.refresh(previous)

    async def test_response_account_change_and_oversize_are_rejected(self):
        previous = parse_tokens(token_payload(self.clock), self.endpoint.binding, self.clock.now)
        for payload in [token_payload(self.clock, account="different-account"), {"access_token": "x" * 70_000}]:
            client = self.client(lambda request, payload=payload: reply(payload))
            with self.subTest(payload_size=len(str(payload))), self.assertRaises(ProviderError):
                await client.refresh(previous)

    async def test_redirect_does_not_send_refresh_to_another_origin(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return reply(status=307, headers={"Location": "http://127.0.0.1:9999/steal"})

        client = self.client(handler)
        with self.assertRaises(ProviderError):
            await client.refresh(parse_tokens(token_payload(self.clock), client.binding, self.clock.now))
        self.assertEqual(seen, [self.endpoint.issuer + "/oauth/token"])

    async def test_request_timeout_and_cancel_propagate_without_replay(self):
        entered = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            await asyncio.Event().wait()
            return reply()

        client = self.client(handler, request_timeout=0.05)
        with self.assertRaises(ProviderError) as caught:
            await client.begin_device_login()
        self.assertEqual(caught.exception.code, ErrorCode.TIMEOUT)
        self.assertEqual(len(calls), 1)
        client = self.client(handler)
        entered.clear()
        task = asyncio.create_task(client.begin_device_login())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(calls), 2)

    async def test_real_loopback_device_and_form_exchange(self):
        async def handler(request, reader, writer):
            if request.path.endswith("/usercode"):
                body = {"user_code": "H2-TEST", "device_auth_id": "device", "interval": 3}
            elif request.path.endswith("/deviceauth/token"):
                body = {"authorization_code": "h2-auth-code", "code_verifier": "v" * 43}
            else:
                self.assertEqual(request.body["grant_type"], "authorization_code")
                body = token_payload(self.clock)
            data = json.dumps(body).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                         + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            await writer.drain()

        async with LocalServer(handler) as server:
            client = CodexAuthClient(AuthEndpoint(server.base_url, local_testing=True),
                                     now=lambda: self.clock.now, monotonic=lambda: self.clock.monotonic, sleep=self.clock.sleep)
            self.clients.append(client)
            tokens = await client.complete_device_login(await client.begin_device_login())
            self.assertEqual(tokens.refresh_token, "h2-synthetic-refresh-new")
            self.assertEqual(len(server.requests), 3)

    async def test_auth_vault_refresh_and_text_provider_integrate(self):
        root = SCRATCH
        root.mkdir(parents=True, exist_ok=True)
        refreshes = []

        def authenticate(request):
            if request.url.path.endswith("/usercode"):
                return reply({"user_code": "H2-TEST", "device_auth_id": "device", "interval": 3})
            if request.url.path.endswith("/deviceauth/token"):
                return reply({"authorization_code": "h2-auth-code", "code_verifier": "v" * 43})
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                refreshes.append(form["refresh_token"][0])
                return reply(token_payload(self.clock, refresh="h2-rotated-final"))
            return reply(token_payload(self.clock, refresh="h2-login-refresh"))

        async def model_reply(request: Request, reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n" + frame(terminal()))
            await writer.drain()

        with tempfile.TemporaryDirectory(dir=root) as folder:
            client = self.client(authenticate)
            async with TokenManager(CredentialStore(Path(folder), client.binding), client, now=lambda: self.clock.now) as manager:
                await manager.login(ignore_challenge)
                async with LocalServer(model_reply) as server:
                    async with CodexTextProvider(model="synthetic-model", session_source=manager.get_session,
                                                 endpoint=CodexEndpoint(server.base_url, local_testing=True)) as model:
                        messages = [{"role": "system", "content": "synthetic"}, {"role": "user", "content": "hi"}]
                        self.assertEqual((await model(messages)).message.content, "连接验证成功")
                        self.clock.now += 3500
                        self.assertEqual((await model(messages)).message.content, "连接验证成功")
                        self.assertEqual(len(server.requests), 2)
                        self.assertEqual(refreshes, ["h2-login-refresh"])

    async def test_http_date_retry_after_is_interpreted_without_real_waiting(self):
        date = format_datetime(datetime.fromtimestamp(self.clock.now + 180, UTC), usegmt=True)
        client = self.client(lambda request: reply(status=429, headers={"Retry-After": date}))
        with self.assertRaises(ProviderError) as caught:
            await client.begin_device_login()
        self.assertEqual(caught.exception.retry_after, 180)

    async def test_late_successful_poll_is_not_exchanged_after_deadline(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path.endswith("/usercode"):
                return reply({"user_code": "H2-TEST", "device_auth_id": "device", "interval": 3, "expires_in": 5})
            self.clock.monotonic += 10
            return reply({"authorization_code": "h2-code", "code_verifier": "v" * 43})

        client = self.client(handler)
        with self.assertRaises(ProviderError) as caught:
            await client.complete_device_login(await client.begin_device_login())
        self.assertEqual(caught.exception.code, ErrorCode.DEVICE_EXPIRED)
        self.assertEqual(len(paths), 2)

    async def test_residency_reaches_inference_headers_with_synthetic_https_transport(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  stream=ByteStream(frame(terminal())))

        original_client = httpx.AsyncClient

        def client_factory(**kwargs):
            # An explicit proxy creates httpx mounts which override the default
            # MockTransport. This synthetic header test must never use them.
            kwargs["proxy"] = None
            return original_client(**kwargs, transport=httpx.MockTransport(handler))

        async def session():
            return CodexSession("h2-synthetic-access", "h2-synthetic-account", "eu")

        with (patch("wechat_agent.providers.codex.transport.application_proxy", return_value="http://127.0.0.1:1"),
              patch("wechat_agent.providers.codex.transport.httpx.AsyncClient", side_effect=client_factory)):
            async with CodexTextProvider(model="synthetic-model", session_source=session) as model:
                await model([{"role": "system", "content": "synthetic"}, {"role": "user", "content": "hi"}])
        self.assertEqual(seen[0].headers["x-openai-internal-codex-residency"], "eu")
        self.assertEqual(seen[0].headers["ChatGPT-Account-ID"], "h2-synthetic-account")
        self.assertEqual(len(seen), 1)
