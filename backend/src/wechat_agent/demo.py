"""Executable H1 composition: real loopback HTTP, synthetic credentials and text."""

import asyncio
import json
from contextlib import suppress

from .providers.codex import CodexEndpoint, CodexTextProvider, SyntheticSession


async def run_demo() -> dict[str, object]:
    tasks: set[asyncio.Task] = set()
    request_count = 0
    fixture = {"type": "response.completed", "response": {
        "id": "resp_h1_demo", "status": "completed", "model": "h1-synthetic-model",
        "output": [{"id": "msg_h1_demo", "type": "message", "role": "assistant",
                    "status": "completed", "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "H1 本机传输验证成功。"}]}],
    }}

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal request_count
        task = asyncio.current_task()
        tasks.add(task)
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode("ascii").split("\r\n")
                fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                fields = {key.lower(): value.strip() for key, value in fields.items()}
                size = int(fields.get("content-length", "0"))
                if size <= 0 or size > 8192:
                    raise ValueError("Invalid synthetic request size")
                payload = json.loads(await reader.readexactly(size))
                if (lines[0] != "POST /responses HTTP/1.1"
                        or fields.get("authorization") != "Bearer h1-synthetic-token"
                        or payload.get("model") != "h1-synthetic-model"):
                    raise ValueError("Unexpected synthetic request")
                request_count += 1
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                             b"Transfer-Encoding: chunked\r\nX-Request-ID: req_h1_demo\r\n"
                             b"Connection: close\r\n\r\n")
                wire = b"data: " + json.dumps(fixture, ensure_ascii=False).encode() + b"\r\n\r\n"
                for index in range(0, len(wire), 7):
                    chunk = wire[index:index + 7]
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
                # The provider must return and close without waiting for HTTP EOF.
                await reader.read()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(task)

    async def session() -> SyntheticSession:
        return SyntheticSession()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, limit=8192)
    try:
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        async with CodexTextProvider(
            model="h1-synthetic-model", session_source=session,
            endpoint=CodexEndpoint(origin, local_testing=True),
        ) as provider:
            result = await provider([
                {"role": "system", "content": "只处理本次合成传输测试。"},
                {"role": "user", "content": "验证本机 HTTP 流。"},
            ])
        return {"mode": "loopback-synthetic", "framework_result": type(result).__module__ + "." + type(result).__name__,
                "http_requests": request_count, "answer": result.message.content,
                "model": result.model, "request_id": result.request_id}
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
