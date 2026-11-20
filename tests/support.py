"""A bounded real HTTP/1.1 test server using only asyncio and synthetic messages."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs


def message(text: str = "连接验证成功", *, phase: str | None = "final_answer", identifier: str = "msg_test") -> dict:
    return {"id": identifier, "type": "message", "role": "assistant", "status": "completed",
            "phase": phase, "content": [{"type": "output_text", "text": text}]}


def terminal(*, output: object = None, status: str = "completed", usage: object = None) -> dict:
    return {"type": "response.completed", "response": {
        "id": "resp_test", "model": "synthetic-model", "status": status,
        "output": [message()] if output is None else output, "usage": usage,
    }}


def frame(event: dict, *, ending: bytes = b"\n", named: bool = True) -> bytes:
    prefix = b"event: " + event["type"].encode() + ending if named else b""
    return prefix + b"data: " + json.dumps(event, ensure_ascii=False).encode() + ending * 2


@dataclass(frozen=True)
class Request:
    path: str
    headers: dict[str, str]
    body: dict


Handler = Callable[[Request, asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


async def headers(writer: asyncio.StreamWriter, *, status: int = 200, extra: bytes = b"") -> None:
    writer.write(f"HTTP/1.1 {status} Test\r\n".encode() + b"Content-Type: text/event-stream\r\n"
                 + b"Connection: close\r\nX-Request-ID: req_test\r\n" + extra + b"\r\n")
    await writer.drain()


class LocalServer:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[Request] = []
        self.errors: list[Exception] = []
        self.tasks: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.received = asyncio.Event()
        self.peer_closed = asyncio.Event()

    async def __aenter__(self) -> "LocalServer":
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.base_url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode("ascii").split("\r\n")
                fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                fields = {k.lower(): v.strip() for k, v in fields.items()}
                size = int(fields.get("content-length", "0"))
                if size > 1_000_000:
                    raise ValueError("Unexpected test request size")
                encoded = await reader.readexactly(size)
                if fields.get("content-type", "").startswith("application/x-www-form-urlencoded"):
                    body = {key: values[0] for key, values in parse_qs(encoded.decode()).items()}
                else:
                    body = json.loads(encoded)
                request = Request(lines[0].split()[1], fields, body)
                self.requests.append(request)
                self.received.set()
                await self.handler(request, reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            self.peer_closed.set()
        except Exception as exc:
            self.errors.append(exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.writers.discard(writer)
            self.tasks.discard(task)

    async def __aexit__(self, *args: object) -> None:
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.errors and (not args or args[0] is None):
            raise AssertionError(f"Local test server failed: {self.errors!r}")


async def serve_answer(request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await headers(writer)
    writer.write(frame(terminal()))
    await writer.drain()
