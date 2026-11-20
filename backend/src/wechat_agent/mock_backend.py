"""Explicit local synthetic OAuth/Responses backend for the interactive mock mode."""

import asyncio
import base64
import json
import time
import uuid
from contextlib import suppress
from urllib.parse import parse_qs


class MockBackend:
    def __init__(self) -> None:
        self.auth_requests = 0
        self.model_requests = 0
        self.last_input: list[dict] = []
        self._tasks: set[asyncio.Task] = set()

    async def __aenter__(self) -> "MockBackend":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=16 * 1024)
        self.origin = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"
        return self

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            async with asyncio.timeout(10):
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode("ascii").split("\r\n")
                fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                fields = {key.lower(): value.strip() for key, value in fields.items()}
                size = int(fields.get("content-length", "0"))
                if not 0 < size <= 512 * 1024:
                    raise ValueError("Mock request limit")
                wire = await reader.readexactly(size)
                body = ({key: values[0] for key, values in parse_qs(wire.decode()).items()}
                        if fields.get("content-type", "").startswith("application/x-www-form-urlencoded")
                        else json.loads(wire))
                path = lines[0].split()[1]
                if path == "/responses":
                    if fields.get("authorization") != "Bearer h1-synthetic-token":
                        raise ValueError("Mock requires synthetic credentials")
                    self.model_requests += 1
                    items = body["input"]
                    last = items[-1]["content"][0]["text"]
                    self.last_input = items
                    text = f"模拟回复（已确认历史 {len(items) - 1} 条）：{last}"
                    payload = {"type": "response.completed", "response": {
                        "id": "resp_" + uuid.uuid4().hex, "status": "completed", "model": body["model"],
                        "output": [{"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                                    "status": "completed", "phase": "final_answer",
                                    "content": [{"type": "output_text", "text": text}]}],
                    }}
                    encoded = b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\r\n\r\n"
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n"
                                 b"X-Request-ID: req_h3_mock\r\n\r\n")
                    for index in range(0, len(encoded), 17):
                        writer.write(encoded[index:index + 17])
                    await writer.drain()
                else:
                    self.auth_requests += 1
                    if path == "/api/accounts/deviceauth/usercode":
                        payload = {"user_code": "H3-MOCK", "device_auth_id": "h3-mock-device", "interval": 3}
                    elif path == "/api/accounts/deviceauth/token":
                        payload = {"authorization_code": "h3-mock-code", "code_verifier": "v" * 43}
                    elif path == "/oauth/token":
                        claims = {"exp": int(time.time()) + 3600,
                                  "https://api.openai.com/auth": {"chatgpt_account_id": "h3-mock-account"}}
                        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
                        payload = {"access_token": "h3-synthetic." + encoded + ".synthetic",
                                   "refresh_token": "h3-mock-refresh-" + uuid.uuid4().hex}
                    else:
                        raise ValueError("Unknown mock route")
                    encoded = json.dumps(payload).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                                 + f"Content-Length: {len(encoded)}\r\n\r\n".encode() + encoded)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError):
            pass
        except (ValueError, KeyError, TypeError):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            with suppress(ConnectionError):
                await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self._tasks.discard(task)

    async def __aexit__(self, *args: object) -> None:
        self._server.close()
        await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self.last_input = []
