"""Owned async HTTP client. No redirects, automatic retries, or error body logging."""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from .config import CodexEndpoint, CodexSession, Limits, SyntheticSession, Timeouts
from .errors import ErrorCode, ProviderError, safe_request_id
from .network import application_proxy
from .sse import decode_events


@dataclass(frozen=True, slots=True)
class EventStream:
    events: AsyncIterator[dict]
    request_id: str | None


class CodexTransport:
    def __init__(self, *, endpoint: CodexEndpoint, limits: Limits, timeouts: Timeouts) -> None:
        self.endpoint = endpoint
        self.limits = limits
        self._closed = False
        self.requests_started = 0
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=timeouts.connect, read=timeouts.read,
                                  write=timeouts.write, pool=timeouts.pool),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False, trust_env=False,
            proxy=application_proxy(local_testing=endpoint.local_testing),
        )

    @asynccontextmanager
    async def open(
        self, payload: dict, session: CodexSession | SyntheticSession,
    ) -> AsyncIterator[EventStream]:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        if self.endpoint.local_testing:
            if type(session) is not SyntheticSession:
                raise ProviderError(ErrorCode.INPUT)
        elif type(session) is not CodexSession:
            raise ProviderError(ErrorCode.INPUT)
        headers = {
            "Authorization": "Bearer " + session.access_token,
            "ChatGPT-Account-ID": session.account_id,
            "originator": "wechat-agent",
            "User-Agent": "WeChatAgent/0.1.0",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        }
        if isinstance(session, CodexSession) and session.residency is not None:
            headers["x-openai-internal-codex-residency"] = session.residency
        request_id = None
        try:
            self.requests_started += 1
            async with self._client.stream(
                "POST", self.endpoint.responses_url, json=payload, headers=headers,
            ) as response:
                request_id = (safe_request_id(response.headers.get("x-request-id"))
                              or safe_request_id(response.headers.get("x-oai-request-id")))
                status = response.status_code
                if status != 200:
                    codes = {401: ErrorCode.AUTH, 403: ErrorCode.FORBIDDEN, 429: ErrorCode.RATE_LIMIT}
                    retry_after = None
                    if status == 429:
                        try:
                            value = float(response.headers.get("retry-after", ""))
                            if math.isfinite(value) and 0 <= value <= 86_400:
                                retry_after = value
                        except ValueError:
                            pass
                    raise ProviderError(codes.get(status, ErrorCode.HTTP), status=status,
                                        request_id=request_id, retry_after=retry_after)
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                encoding = response.headers.get("content-encoding", "identity").strip().lower()
                # The live Codex endpoint can omit Content-Type on a valid SSE
                # response. Missing metadata does not bypass bounded SSE parsing
                # or the mandatory response.completed check in collect_result.
                if (("content-type" in response.headers and media_type != "text/event-stream")
                        or encoding not in {"", "identity"}):
                    raise ProviderError(ErrorCode.PROTOCOL, request_id=request_id)
                events = decode_events(response.aiter_raw(), self.limits)
                try:
                    yield EventStream(events=events, request_id=request_id)
                finally:
                    await events.aclose()
        except ProviderError as exc:
            if exc.request_id is None:
                exc.request_id = request_id
            raise
        except httpx.TimeoutException:
            raise ProviderError(ErrorCode.TIMEOUT, request_id=request_id) from None
        except httpx.HTTPError:
            raise ProviderError(ErrorCode.CONNECTION, request_id=request_id) from None

    async def aclose(self) -> None:
        self._closed = True
        await self._client.aclose()
