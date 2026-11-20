"""Independent device-code OAuth and refresh HTTP; no external credential adoption."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from .auth_models import (
    AuthEndpoint,
    DeviceChallenge,
    TokenSet,
    bounded_text,
    finite_number,
    json_object,
    parse_tokens,
)
from .errors import ErrorCode, ProviderError, safe_request_id
from .network import application_proxy


@dataclass(frozen=True, slots=True)
class AuthReply:
    status: int
    body: bytes = field(repr=False)
    request_id: str | None
    retry_after: float | None


class CodexAuthClient:
    def __init__(
        self, endpoint: AuthEndpoint | None = None, *, request_timeout: float = 20,
        now: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint or AuthEndpoint()
        self.binding = self.endpoint.binding
        self._timeout = finite_number(request_timeout)
        self._now, self._monotonic, self._sleep = now, monotonic, sleep
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout), follow_redirects=False, trust_env=False,
            proxy=application_proxy(local_testing=self.endpoint.local_testing, injected_transport=transport is not None),
            limits=httpx.Limits(max_connections=1), transport=transport,
            headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "WeChatAgent/0.1.0"},
        )
        self._closed = False

    async def _post(self, path: str, *, form: dict | None = None, data: dict | None = None) -> AuthReply:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream("POST", self.binding.issuer + path, data=form, json=data) as response:
                    request_id = safe_request_id(response.headers.get("x-request-id"))
                    retry = None
                    try:
                        seconds = float(response.headers.get("retry-after", ""))
                        if math.isfinite(seconds) and 0 <= seconds <= 86_400:
                            retry = seconds
                    except ValueError:
                        try:
                            date = parsedate_to_datetime(response.headers.get("retry-after", ""))
                            if date.tzinfo is not None:
                                retry = min(86_400, max(0, date.timestamp() - self._now()))
                        except (ValueError, TypeError, OverflowError):
                            pass
                    if response.status_code in {403, 404} and path.endswith("/deviceauth/token"):
                        return AuthReply(response.status_code, b"", request_id, retry)
                    if response.status_code == 429:
                        return AuthReply(429, b"", request_id, retry)
                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise ProviderError(ErrorCode.PROTOCOL, request_id=request_id)
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > 64 * 1024:
                            raise ProviderError(ErrorCode.LIMIT, request_id=request_id)
                    return AuthReply(response.status_code, bytes(body), request_id, retry)
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderError(ErrorCode.CONNECTION) from None

    @staticmethod
    def _success(reply: AuthReply) -> dict:
        if reply.status == 429:
            raise ProviderError(ErrorCode.RATE_LIMIT, status=429, request_id=reply.request_id,
                                retry_after=reply.retry_after)
        if reply.status != 200:
            code = ErrorCode.AUTH if reply.status in {400, 401} else ErrorCode.FORBIDDEN if reply.status == 403 else ErrorCode.HTTP
            if reply.status == 403:
                # Classify only this known machine code; remote text stays private.
                try:
                    error = json_object(reply.body).get("error")
                    if isinstance(error, dict) and error.get("code") == "unsupported_country_region_territory":
                        code = ErrorCode.REGION
                except ProviderError:
                    pass
            raise ProviderError(code, status=reply.status, request_id=reply.request_id)
        try:
            return json_object(reply.body)
        except ProviderError as exc:
            exc.request_id = reply.request_id
            raise

    async def begin_device_login(self) -> DeviceChallenge:
        reply = await self._post("/api/accounts/deviceauth/usercode", data={"client_id": self.binding.client_id})
        body = self._success(reply)
        interval = body.get("interval", 5)
        if isinstance(interval, str) and len(interval) <= 4 and interval.isascii() and interval.isdecimal():
            interval = int(interval)
        interval = max(3, finite_number(interval))
        lifetime = min(900, finite_number(body.get("expires_in", 900)))
        return DeviceChallenge(
            self.binding.issuer + "/codex/device",
            bounded_text(body.get("user_code"), maximum=64),
            bounded_text(body.get("device_auth_id"), maximum=512),
            interval, self._monotonic() + lifetime,
        )

    async def complete_device_login(self, challenge: DeviceChallenge) -> TokenSet:
        if challenge.verification_uri != self.binding.issuer + "/codex/device":
            raise ProviderError(ErrorCode.INPUT)
        while True:
            remaining = challenge.deadline - self._monotonic()
            if remaining <= challenge.interval:
                raise ProviderError(ErrorCode.DEVICE_EXPIRED)
            await self._sleep(challenge.interval)
            remaining = challenge.deadline - self._monotonic()
            if remaining <= 0:
                raise ProviderError(ErrorCode.DEVICE_EXPIRED)
            try:
                async with asyncio.timeout(remaining):
                    reply = await self._post("/api/accounts/deviceauth/token", data={
                        "device_auth_id": challenge.device_auth_id, "user_code": challenge.user_code,
                    })
            except TimeoutError:
                raise ProviderError(ErrorCode.DEVICE_EXPIRED) from None
            if reply.status in {403, 404}:
                continue
            authorization = self._success(reply)
            if self._monotonic() >= challenge.deadline:
                raise ProviderError(ErrorCode.DEVICE_EXPIRED)
            code = bounded_text(authorization.get("authorization_code"), maximum=4096)
            verifier = bounded_text(authorization.get("code_verifier"), maximum=128)
            reply = await self._post("/oauth/token", form={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.binding.issuer + "/deviceauth/callback",
                "client_id": self.binding.client_id, "code_verifier": verifier,
            })
            return self._tokens(reply)

    def _tokens(self, reply: AuthReply, previous: TokenSet | None = None) -> TokenSet:
        try:
            return parse_tokens(self._success(reply), self.binding, self._now(), previous)
        except ProviderError as exc:
            exc.request_id = reply.request_id
            raise

    async def refresh(self, previous: TokenSet) -> TokenSet:
        if previous.binding != self.binding:
            raise ProviderError(ErrorCode.INPUT)
        reply = await self._post("/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": previous.refresh_token,
            "client_id": self.binding.client_id,
        })
        return self._tokens(reply, previous)

    async def aclose(self) -> None:
        self._closed = True
        await self._client.aclose()
