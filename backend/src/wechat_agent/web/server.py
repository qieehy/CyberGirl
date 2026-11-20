"""Loopback HTTP lifecycle, authenticated origin checks and static build serving."""

import asyncio
import json
import secrets

from ..social.models import SocialError
from .assets import web_root
from .validation import parse_json


class HttpServer:
    def __init__(self, *, token=None, static=None):
        self.token = token or secrets.token_urlsafe(32)
        self.server = None
        self.tasks = set()
        self.static = static or {}
        self.asset_root = web_root()

    async def start(self, port=8765):
        if type(port) is not int or not 0 <= port <= 65535:
            raise SocialError("invalid_port")
        try:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", port, limit=16384)
        except OSError:
            raise SocialError("console_port_unavailable") from None
        self.port = self.server.sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        return self.origin + "/#auth=" + self.token

    async def aclose(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _respond(self, writer, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = [
            f"HTTP/1.1 {status} Response",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "Cache-Control: no-store",
            "X-Content-Type-Options: nosniff",
            "Referrer-Policy: no-referrer",
            "X-Frame-Options: DENY",
            "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        ]
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body)
        await writer.drain()

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= 16:
            writer.close()
            return
        self.tasks.add(task)
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 16384:
                    raise ValueError("header limit")
                lines = header.decode("ascii").split("\r\n")
                method, path, protocol = lines[0].split(" ")
                if protocol != "HTTP/1.1" or method not in {"GET", "POST"}:
                    raise ValueError("request")
                headers = {}
                for line in lines[1:-2]:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    if key in headers:
                        raise ValueError("duplicate header")
                    headers[key] = value.strip()
                if "transfer-encoding" in headers:
                    raise ValueError("unsupported transfer")
                size = int(headers.get("content-length", "0"))
                if not 0 <= size <= 32768 or (method == "GET" and size):
                    raise ValueError("body limit")
                if headers.get("host") != f"127.0.0.1:{self.port}":
                    await self._respond(writer, 403, {"error": "invalid_host"})
                    return
                if method == "GET" and path in self.static:
                    filename, media_type = self.static[path]
                    await self._respond(
                        writer, 200, (self.asset_root / filename).read_bytes(), media_type
                    )
                    return
                origin = headers.get("origin")
                if (origin is not None and origin != self.origin) or (
                    method == "POST" and origin != self.origin
                ):
                    await self._respond(writer, 403, {"error": "invalid_origin"})
                    return
                if not secrets.compare_digest(
                    headers.get("authorization", ""), "Bearer " + self.token
                ):
                    await self._respond(writer, 401, {"error": "authentication_required"})
                    return
                if (
                    method == "POST"
                    and headers.get("content-type", "").split(";", 1)[0].strip()
                    != "application/json"
                ):
                    raise ValueError("content type")
                body = await reader.readexactly(size)
            data = parse_json(body) if method == "POST" else None
            async with asyncio.timeout(125):
                result = await self._route(method, path, data)
            await self._respond(writer, 200, result)
        except SocialError as exc:
            status = 409 if exc.code in {"version_conflict", "event_identity_conflict"} else 400
            if exc.code in {"unknown_event", "unknown_action", "not_found"}:
                status = 404
            if exc.code == "busy":
                status = 429
            await self._respond(writer, status, {"error": exc.code})
        except (
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            RecursionError,
        ):
            await self._respond(writer, 400, {"error": "invalid_request"})
        except TimeoutError:
            await self._respond(writer, 408, {"error": "request_timeout"})
        except (ConnectionError, OSError):
            pass
        except Exception:
            await self._respond(writer, 500, {"error": "internal_error"})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.tasks.discard(task)
