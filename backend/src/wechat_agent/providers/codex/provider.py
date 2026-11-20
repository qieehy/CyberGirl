"""Framework callable with bounded admission, total deadline and explicit ownership."""

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable

from llm.models import LLMResult

from .config import CodexEndpoint, CodexSession, Limits, SyntheticSession, Timeouts
from .errors import ErrorCode, ProviderError
from .response import collect_result
from .transport import CodexTransport

SessionSource = Callable[[], Awaitable[CodexSession | SyntheticSession]]
SessionHook = Callable[[CodexSession | SyntheticSession], Awaitable[None]]


def build_request(messages: list[dict], tools: list[dict] | None, *, model: str, limits: Limits) -> dict:
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ProviderError(ErrorCode.INPUT)
    if tools is not None and (not isinstance(tools, list) or tools):
        raise ProviderError(ErrorCode.INPUT)
    if not isinstance(messages, list) or not 2 <= len(messages) <= limits.messages:
        raise ProviderError(ErrorCode.INPUT)
    items = []
    instructions = ""
    total = 0
    images = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ProviderError(ErrorCode.INPUT)
        role, text = message["role"], message["content"]
        parts = None
        if isinstance(text, list) and role == "user" and index > 0:
            images += 1
            if images > 1:
                raise ProviderError(ErrorCode.LIMIT)
            if len(text) != 2 or not all(isinstance(p, dict) for p in text):
                raise ProviderError(ErrorCode.INPUT)
            words, picture = text
            if (set(words) != {"type", "text"} or words["type"] != "input_text"
                    or not isinstance(words["text"], str)
                    or set(picture) != {"type", "image_url"} or picture["type"] != "input_image"):
                raise ProviderError(ErrorCode.INPUT)
            url = picture["image_url"]
            prefix = "data:image/jpeg;base64,"
            if not isinstance(url, str) or not url.startswith(prefix) or len(url) > 2_800_000:
                raise ProviderError(ErrorCode.INPUT)
            try:
                image = base64.b64decode(url[len(prefix):], validate=True)
            except (ValueError, binascii.Error):
                raise ProviderError(ErrorCode.INPUT) from None
            if not image.startswith(b"\xff\xd8\xff") or len(image) > 2 * 1024 * 1024:
                raise ProviderError(ErrorCode.INPUT)
            parts, text = [dict(words), dict(picture)], words["text"]
        if not isinstance(text, str):
            raise ProviderError(ErrorCode.INPUT)
        total += len(text)
        if total > limits.input_chars:
            raise ProviderError(ErrorCode.LIMIT)
        if index == 0:
            if role != "system" or not text.strip():
                raise ProviderError(ErrorCode.INPUT)
            instructions = text
        else:
            if role not in ("user", "assistant"):
                raise ProviderError(ErrorCode.INPUT)
            items.append({"type": "message", "role": role, "content": parts or [
                {"type": "input_text" if role == "user" else "output_text", "text": text}
            ]})
    if items[-1]["role"] != "user":
        raise ProviderError(ErrorCode.INPUT)
    return {"model": model.strip(), "instructions": instructions,
            "input": items, "store": False, "stream": True}


class CodexTextProvider:
    """Owns transport and pending calls; use async with or call aclose().

    Credential source is injected; H1 implements no login or refresh. Calls execute
    serially; queue waiting counts against the same total deadline as HTTP.
    """

    def __init__(
        self, *, model: str, session_source: SessionSource,
        endpoint: CodexEndpoint | None = None,
        limits: Limits | None = None, timeouts: Timeouts | None = None,
        recover_session: SessionHook | None = None, invalidate_session: SessionHook | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("A selected model is required")
        endpoint = endpoint if endpoint is not None else CodexEndpoint()
        limits = limits if limits is not None else Limits()
        timeouts = timeouts if timeouts is not None else Timeouts()
        self.model = model.strip()
        self.limits = limits
        self.timeouts = timeouts
        self._source = session_source
        self._recover, self._invalidate = recover_session, invalidate_session
        self._transport = CodexTransport(endpoint=endpoint, limits=limits, timeouts=timeouts)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def __call__(self, messages: list[dict], tools: list[dict] | None = None, *,
                       context_key: str | None = None) -> LLMResult:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        payload = build_request(messages, tools, model=self.model, limits=self.limits)
        if len(self._tasks) >= self.limits.pending_calls:
            raise ProviderError(ErrorCode.BUSY)
        deadline = asyncio.get_running_loop().time() + self.timeouts.total
        task = asyncio.create_task(self._run(payload, deadline, context_key), name="codex-text-request")
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)

    async def _run(self, payload: dict, expires_at: float, context_key: str | None) -> LLMResult:
        try:
            async with asyncio.timeout_at(expires_at):
                async with self._lock:
                    original_epoch = context_key
                    for attempt in range(2):
                        session = await self._source()
                        if original_epoch is not None and session.context_key != original_epoch:
                            raise ProviderError(ErrorCode.AUTH)
                        original_epoch = session.context_key
                        try:
                            async with self._transport.open(payload, session) as stream:
                                result = await collect_result(stream.events, limits=self.limits,
                                                              selected_model=self.model,
                                                              request_id=stream.request_id)
                            break
                        except ProviderError as exc:
                            if exc.code != ErrorCode.AUTH or exc.status != 401:
                                raise
                            if attempt == 0 and self._recover is not None:
                                await self._recover(session)
                                continue
                            if self._invalidate is not None:
                                await self._invalidate(session)
                            raise
                # JSON/UTF-8 parsing is synchronous but bounded. A completed result
                # must not escape a deadline that elapsed before the loop ran its timer.
                if asyncio.get_running_loop().time() >= expires_at:
                    raise ProviderError(ErrorCode.TIMEOUT)
                return result
        except TimeoutError:
            raise ProviderError(ErrorCode.TIMEOUT) from None

    @property
    def http_attempts(self) -> int:
        """Transport attempts, including the optional controlled HTTP 401 replay."""
        return self._transport.requests_started

    async def __aenter__(self) -> "CodexTextProvider":
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            current = asyncio.current_task()
            pending = [task for task in self._tasks if task is not current]
            self._close_task = asyncio.create_task(self._finish_close(pending), name="codex-text-close")
        # A cancelled shutdown waiter must not cancel stream/client cleanup. All
        # shutdown callers join the same owned task instead of cancelling twice.
        await asyncio.shield(self._close_task)

    async def _finish_close(self, pending: list[asyncio.Task]) -> None:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self._transport.aclose()
