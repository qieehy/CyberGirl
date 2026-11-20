import asyncio
import unittest
from dataclasses import replace

from tests.support import LocalServer, frame, headers, serve_answer, terminal
from wechat_agent.providers.codex import (
    CodexEndpoint,
    CodexSession,
    CodexTextProvider,
    ErrorCode,
    Limits,
    ProviderError,
    SyntheticSession,
    Timeouts,
)

MESSAGES = [{"role": "system", "content": "你是透明的 AI。"}, {"role": "user", "content": "你好"}]


async def synthetic():
    return SyntheticSession()


def provider(server, **kwargs):
    return CodexTextProvider(model="synthetic-model", session_source=synthetic,
                             endpoint=CodexEndpoint(server.base_url, local_testing=True), **kwargs)


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_chunked_http_request_utf8_and_framework_result(self):
        async def handler(request, reader, writer):
            await headers(writer, extra=b"Transfer-Encoding: chunked\r\n")
            data = frame(terminal(usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}), ending=b"\r\n")
            for byte in data:
                writer.write(b"1\r\n" + bytes([byte]) + b"\r\n")
            writer.write(b"0\r\n\r\n")
            await writer.drain()

        async with LocalServer(handler) as server, provider(server) as model:
            result = await model(MESSAGES)
            self.assertEqual(result.message.content, "连接验证成功")
            self.assertEqual(result.request_id, "req_test")
            self.assertEqual(result.usage.total_tokens, 5)
            request = server.requests[0]
            self.assertEqual(request.path, "/responses")
            self.assertEqual(request.body["instructions"], MESSAGES[0]["content"])
            self.assertEqual(request.headers["authorization"], "Bearer h1-synthetic-token")
            self.assertEqual(request.headers["originator"], "wechat-agent")
            self.assertNotIn("tools", request.body)
            self.assertEqual(len(server.requests), 1)
            self.assertFalse(server.errors)

    async def test_complete_without_eof_returns_and_closes_peer(self):
        async def handler(request, reader, writer):
            await headers(writer)
            writer.write(frame(terminal()))
            await writer.drain()
            await reader.read()
            server.peer_closed.set()

        async with LocalServer(handler) as server, provider(server) as model:
            result = await asyncio.wait_for(model(MESSAGES), 2)
            self.assertEqual(result.message.content, "连接验证成功")
            await asyncio.wait_for(server.peer_closed.wait(), 1)

    async def test_http_200_partial_stream_never_returns_a_result(self):
        async def handler(request, reader, writer):
            await headers(writer)
            writer.write(frame({"type": "response.output_text.delta", "delta": "partial"}))
            await writer.drain()

        async with LocalServer(handler) as server, provider(server) as model:
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.INCOMPLETE)
            self.assertEqual(caught.exception.request_id, "req_test")
            self.assertEqual(len(server.requests), 1)

    async def test_bad_json_and_wrong_event_type_close_connection(self):
        for data in [b"data: not-json\n\n", b"event: error\n" + frame(terminal(), named=False)]:
            async def handler(request, reader, writer, data=data):
                await headers(writer)
                writer.write(data)
                await writer.drain()
                await reader.read()
                server.peer_closed.set()

            async with LocalServer(handler) as server, provider(server) as model:
                with self.assertRaises(ProviderError):
                    await model(MESSAGES)
                await asyncio.wait_for(server.peer_closed.wait(), 1)

    async def test_http_errors_are_typed_do_not_read_body_or_retry(self):
        for status, code in [(401, ErrorCode.AUTH), (403, ErrorCode.FORBIDDEN),
                             (429, ErrorCode.RATE_LIMIT), (503, ErrorCode.HTTP)]:
            async def handler(request, reader, writer, status=status):
                await headers(writer, status=status, extra=b"Retry-After: 7\r\nContent-Length: 999999\r\n")
                await reader.read()
                server.peer_closed.set()

            with self.subTest(status=status):
                async with LocalServer(handler) as server, provider(server) as model:
                    with self.assertRaises(ProviderError) as caught:
                        await asyncio.wait_for(model(MESSAGES), 2)
                    error = caught.exception
                    self.assertEqual(error.code, code)
                    self.assertEqual(error.status, status)
                    self.assertEqual(error.request_id, "req_test")
                    self.assertEqual(error.retry_after, 7 if status == 429 else None)
                    self.assertEqual(len(server.requests), 1)
                    await asyncio.wait_for(server.peer_closed.wait(), 1)

    async def test_redirect_cannot_forward_credentials(self):
        async with LocalServer(serve_answer) as destination:
            async def redirect(request, reader, writer):
                await headers(writer, status=307,
                              extra=f"Location: {destination.base_url}/responses\r\n".encode())

            async with LocalServer(redirect) as server, provider(server) as model:
                with self.assertRaises(ProviderError) as caught:
                    await model(MESSAGES)
                self.assertEqual(caught.exception.code, ErrorCode.HTTP)
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(destination.requests, [])

    async def test_non_sse_and_compressed_success_are_rejected(self):
        for header in [b"Content-Type: application/json\r\n", b"Content-Encoding: gzip\r\n"]:
            async def handler(request, reader, writer, header=header):
                writer.write(b"HTTP/1.1 200 OK\r\n" + header + b"Connection: close\r\n\r\n")
                writer.write(frame(terminal()))
                await writer.drain()

            async with LocalServer(handler) as server, provider(server) as model:
                with self.assertRaises(ProviderError) as caught:
                    await model(MESSAGES)
                self.assertEqual(caught.exception.code, ErrorCode.PROTOCOL)

    async def test_read_timeout_and_total_deadline_close_connection(self):
        async def handler(request, reader, writer):
            await headers(writer)
            await reader.read()
            server.peer_closed.set()

        cases = [Timeouts(read=0.1, total=5), Timeouts(read=5, total=2)]
        for timeouts in cases:
            with self.subTest(timeouts=timeouts):
                async with LocalServer(handler) as server, provider(server, timeouts=timeouts) as model:
                    with self.assertRaises(ProviderError) as caught:
                        await model(MESSAGES)
                    self.assertEqual(caught.exception.code, ErrorCode.TIMEOUT)
                    self.assertEqual(len(server.requests), 1, "Timeout must occur on an established stream")
                    await asyncio.wait_for(server.peer_closed.wait(), 1)
                    self.assertEqual(len(server.requests), 1)

    async def test_heartbeat_does_not_extend_total_deadline(self):
        async def handler(request, reader, writer):
            await headers(writer)
            while not reader.at_eof():
                writer.write(b": heartbeat\n\n")
                await writer.drain()
                try:
                    await asyncio.wait_for(reader.read(1), 0.01)
                except TimeoutError:
                    continue
            server.peer_closed.set()

        async with LocalServer(handler) as server, provider(server, timeouts=Timeouts(read=1, total=2)) as model:
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.TIMEOUT)
            self.assertEqual(len(server.requests), 1, "Heartbeat stream must actually be established")
            await asyncio.wait_for(server.peer_closed.wait(), 1)

    async def test_external_cancellation_closes_stream_and_provider_remains_usable(self):
        entered = asyncio.Event()

        async def handler(request, reader, writer):
            await headers(writer)
            if len(server.requests) == 1:
                writer.write(frame({"type": "response.output_text.delta", "delta": "partial"}))
                await writer.drain()
                entered.set()
                await reader.read()
                server.peer_closed.set()
            else:
                writer.write(frame(terminal()))
                await writer.drain()

        async with LocalServer(handler) as server, provider(server) as model:
            task = asyncio.create_task(model(MESSAGES))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(server.peer_closed.wait(), 1)
            self.assertEqual((await model(MESSAGES)).message.content, "连接验证成功")
            self.assertEqual(len(server.requests), 2)

    async def test_provider_close_cancels_active_and_queued_calls(self):
        async def handler(request, reader, writer):
            await headers(writer)
            await reader.read()
            server.peer_closed.set()

        async with LocalServer(handler) as server:
            model = provider(server)
            first = asyncio.create_task(model(MESSAGES))
            await asyncio.wait_for(server.received.wait(), 1)
            second = asyncio.create_task(model(MESSAGES))
            await asyncio.sleep(0)
            await model.aclose()
            self.assertTrue(first.cancelled())
            self.assertTrue(second.cancelled())
            await asyncio.wait_for(server.peer_closed.wait(), 1)
            self.assertEqual(len(server.requests), 1)
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.CLOSED)
            await model.aclose()

    async def test_bounded_admission_and_serial_requests(self):
        release = asyncio.Event()

        async def handler(request, reader, writer):
            await headers(writer)
            await release.wait()
            writer.write(frame(terminal()))
            await writer.drain()

        async with LocalServer(handler) as server, provider(server, limits=replace(Limits(), pending_calls=1)) as model:
            task = asyncio.create_task(model(MESSAGES))
            await asyncio.wait_for(server.received.wait(), 1)
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.BUSY)
            release.set()
            self.assertEqual((await task).message.content, "连接验证成功")
            self.assertEqual((await model(MESSAGES)).message.content, "连接验证成功")
            self.assertEqual(len(server.requests), 2)

    async def test_real_credentials_are_never_accepted_at_test_endpoint(self):
        async def real_session():
            return CodexSession("synthetic-but-real-session-type", "synthetic-account")

        async with LocalServer(serve_answer) as server:
            async with CodexTextProvider(model="synthetic-model", session_source=real_session,
                                         endpoint=CodexEndpoint(server.base_url, local_testing=True)) as model:
                with self.assertRaises(ProviderError) as caught:
                    await model(MESSAGES)
                self.assertEqual(caught.exception.code, ErrorCode.INPUT)
                self.assertEqual(server.requests, [])

    async def test_bad_input_is_rejected_before_credentials_or_http(self):
        called = False

        async def source():
            nonlocal called
            called = True
            return SyntheticSession()

        async with LocalServer(serve_answer) as server:
            async with CodexTextProvider(model="synthetic-model", session_source=source,
                                         endpoint=CodexEndpoint(server.base_url, local_testing=True)) as model:
                with self.assertRaises(ProviderError):
                    await model(MESSAGES, [{"type": "function"}])
                self.assertFalse(called)
                self.assertEqual(server.requests, [])

    async def test_wire_limits_abort_and_close(self):
        async def handler(request, reader, writer):
            await headers(writer)
            writer.write(b"data: " + b"x" * 1000)
            await writer.drain()
            await reader.read()
            server.peer_closed.set()

        async with LocalServer(handler) as server, provider(server, limits=replace(Limits(), line_bytes=100)) as model:
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.LIMIT)
            await asyncio.wait_for(server.peer_closed.wait(), 1)

    async def test_credential_wait_counts_toward_total_deadline(self):
        async def waiting_source():
            await asyncio.Event().wait()
            return SyntheticSession()

        async with LocalServer(serve_answer) as server:
            async with CodexTextProvider(model="synthetic-model", session_source=waiting_source,
                                         endpoint=CodexEndpoint(server.base_url, local_testing=True),
                                         timeouts=Timeouts(total=0.05)) as model:
                with self.assertRaises(ProviderError) as caught:
                    await model(MESSAGES)
                self.assertEqual(caught.exception.code, ErrorCode.TIMEOUT)
                self.assertEqual(server.requests, [])

    async def test_shutdown_waits_for_owned_work_not_caller_recovery(self):
        recovering = asyncio.Event()
        release = asyncio.Event()

        async def handler(request, reader, writer):
            await headers(writer)
            await reader.read()
            server.peer_closed.set()

        async with LocalServer(handler) as server:
            model = provider(server)

            async def caller():
                try:
                    await model(MESSAGES)
                except asyncio.CancelledError:
                    recovering.set()
                    await release.wait()

            task = asyncio.create_task(caller())
            try:
                await asyncio.wait_for(server.received.wait(), 1)
                await asyncio.wait_for(model.aclose(), 1)
                await asyncio.wait_for(recovering.wait(), 1)
                self.assertFalse(task.done())
                await asyncio.wait_for(server.peer_closed.wait(), 1)
            finally:
                release.set()
                await task
                await model.aclose()

    async def test_cancelled_shutdown_waiter_does_not_cancel_cleanup(self):
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def source():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
            return SyntheticSession()

        async with LocalServer(serve_answer) as server:
            model = CodexTextProvider(model="synthetic-model", session_source=source,
                                      endpoint=CodexEndpoint(server.base_url, local_testing=True))
            request = asyncio.create_task(model(MESSAGES))
            shutdown = None
            try:
                await entered.wait()
                shutdown = asyncio.create_task(model.aclose())
                await asyncio.wait_for(cleaning.wait(), 1)
                shutdown.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await shutdown
                release.set()
                await asyncio.wait_for(model.aclose(), 1)
                with self.assertRaises(asyncio.CancelledError):
                    await request
                self.assertEqual(server.requests, [])
            finally:
                release.set()
                await model.aclose()
                await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await asyncio.gather(shutdown, return_exceptions=True)

    async def test_queue_wait_counts_toward_same_total_deadline(self):
        entered = asyncio.Event()
        sources = 0

        async def waiting_source():
            nonlocal sources
            sources += 1
            entered.set()
            await asyncio.Event().wait()
            return SyntheticSession()

        async with LocalServer(serve_answer) as server:
            async with CodexTextProvider(model="synthetic-model", session_source=waiting_source,
                                         endpoint=CodexEndpoint(server.base_url, local_testing=True),
                                         timeouts=Timeouts(total=0.1)) as model:
                first = asyncio.create_task(model(MESSAGES))
                await entered.wait()
                second = asyncio.create_task(model(MESSAGES))
                results = await asyncio.gather(first, second, return_exceptions=True)
                self.assertTrue(all(isinstance(result, ProviderError) and result.code == ErrorCode.TIMEOUT for result in results))
                self.assertEqual(server.requests, [])
                self.assertLessEqual(sources, 2)

    async def test_connection_failure_is_typed_and_not_retried(self):
        async with LocalServer(serve_answer) as server:
            pass
        async with provider(server) as model:
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.CONNECTION)
            self.assertIsNone(caught.exception.request_id)

    async def test_real_http_eof_without_blank_event_delimiter_is_incomplete(self):
        async def handler(request, reader, writer):
            await headers(writer)
            writer.write(frame(terminal())[:-1])
            await writer.drain()

        async with LocalServer(handler) as server, provider(server) as model:
            with self.assertRaises(ProviderError) as caught:
                await model(MESSAGES)
            self.assertEqual(caught.exception.code, ErrorCode.INCOMPLETE)


class ConfigurationTests(unittest.TestCase):
    def test_official_endpoint_and_credential_headers_are_bound(self):
        for url in ["https://example.com", "https://chatgpt.com.evil/backend-api/codex",
                    "https://chatgpt.com/backend-api/codex/", "http://chatgpt.com/backend-api/codex"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                CodexEndpoint(url)
        for url in ["http://localhost:8000", "http://127.0.0.1:8000/x", "http://user@127.0.0.1:8000",
                    "http://127.0.0.1:8000?x=1", "http://0.0.0.0:8000", "https://127.0.0.1:8000"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                CodexEndpoint(url, local_testing=True)
        self.assertEqual(CodexEndpoint().responses_url, "https://chatgpt.com/backend-api/codex/responses")
        self.assertNotIn("secret-token", repr(CodexSession("secret-token", "account")))
        for token in ["", "line\r\ninjected", "space value", "非ASCII"]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                CodexSession(token, "account")

    def test_invalid_bounds_are_rejected(self):
        for kwargs in [{"read": 0}, {"total": float("nan")}, {"connect": float("inf")}, {"pool": True}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Timeouts(**kwargs)
        for kwargs in [{"events": 0}, {"input_chars": True}, {"pending_calls": -1}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)
