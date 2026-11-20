"""Bounded account-scoped model discovery; no static or synthetic model fallback."""

import asyncio
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import httpx

from .auth_models import json_object
from .config import CodexEndpoint, CodexSession, SyntheticSession
from .errors import ErrorCode, ProviderError, safe_request_id
from .network import application_proxy

# Protocol compatibility version for /models, not the application's version.
CATALOG_CLIENT_VERSION = "0.164.0"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class CatalogModel:
    slug: str
    priority: int


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    models: tuple[CatalogModel, ...]
    fetched_at: str
    request_id: str | None
    context_key: str | None = field(default=None, repr=False)

    def contains(self, slug: str) -> bool:
        return any(model.slug == slug for model in self.models)

    def summary(self) -> dict:
        return {"source": "live_account_catalog", "client_version": CATALOG_CLIENT_VERSION,
                "fetched_at": self.fetched_at, "request_id": self.request_id,
                "models": [asdict(model) for model in self.models],
                "note": "Directory visibility is not proof of a successful inference request."}


def _models(body: bytes) -> tuple[CatalogModel, ...]:
    entries = json_object(body).get("models")
    if not isinstance(entries, list) or len(entries) > 256:
        raise ProviderError(ErrorCode.PROTOCOL)
    models = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ProviderError(ErrorCode.PROTOCOL)
        slug = item.get("slug")
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise ProviderError(ErrorCode.PROTOCOL)
        visibility = item.get("visibility", "list")
        if not isinstance(visibility, str):
            raise ProviderError(ErrorCode.PROTOCOL)
        if visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority", 10_000)
        if type(priority) is not int or not -1_000_000 <= priority <= 1_000_000:
            priority = 10_000
        # supported_in_api describes the public API, not subscription access.
        if slug not in models or priority < models[slug].priority:
            models[slug] = CatalogModel(slug, priority)
    return tuple(sorted(models.values(), key=lambda model: (model.priority, model.slug)))


async def fetch_catalog(
    session: CodexSession | SyntheticSession, *, endpoint: CodexEndpoint | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelCatalog:
    endpoint = endpoint or CodexEndpoint()
    expected = SyntheticSession if endpoint.local_testing else CodexSession
    if type(session) is not expected:
        raise ProviderError(ErrorCode.INPUT)
    headers = {"Authorization": "Bearer " + session.access_token,
               "ChatGPT-Account-ID": session.account_id, "originator": "wechat-agent",
               "User-Agent": "WeChatAgent/0.1.0", "Accept": "application/json",
               "Accept-Encoding": "identity"}
    if isinstance(session, CodexSession) and session.residency is not None:
        headers["x-openai-internal-codex-residency"] = session.residency
    request_id = None
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(
            timeout=15, follow_redirects=False, trust_env=False, transport=transport,
            proxy=application_proxy(local_testing=endpoint.local_testing, injected_transport=transport is not None),
            limits=httpx.Limits(max_connections=1),
        ) as client:
            async with client.stream("GET", endpoint.base_url + "/models",
                                     params={"client_version": CATALOG_CLIENT_VERSION}, headers=headers) as response:
                request_id = safe_request_id(response.headers.get("x-request-id"))
                if response.status_code != 200:
                    status = response.status_code
                    code = {401: ErrorCode.AUTH, 403: ErrorCode.FORBIDDEN,
                            429: ErrorCode.RATE_LIMIT}.get(status, ErrorCode.HTTP)
                    retry_after = None
                    if status == 429:
                        try:
                            seconds = float(response.headers.get("retry-after", ""))
                            if math.isfinite(seconds) and 0 <= seconds <= 86_400:
                                retry_after = seconds
                        except ValueError:
                            pass
                    raise ProviderError(code, status=status, request_id=request_id, retry_after=retry_after)
                if (response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}):
                    raise ProviderError(ErrorCode.PROTOCOL)
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > MAX_CATALOG_BYTES:
                        raise ProviderError(ErrorCode.LIMIT)
                    body.extend(chunk)
                models = _models(bytes(body))
                return ModelCatalog(models, datetime.now(UTC).isoformat(), request_id, session.context_key)
    except ProviderError as exc:
        if exc.request_id is None:
            exc.request_id = request_id
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise ProviderError(ErrorCode.TIMEOUT, request_id=request_id) from None
    except httpx.HTTPError:
        raise ProviderError(ErrorCode.CONNECTION, request_id=request_id) from None
