"""Credential values and bounded parsing; JWT claims are routing hints, not proof."""

import base64
import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

from .config import OFFICIAL_BASE, CodexSession
from .errors import ErrorCode, ProviderError

AUTH_ISSUER = "https://auth.openai.com"
# Public protocol configuration observed in the pinned Hermes Codex provider.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def bounded_text(value: object, *, maximum: int, code: ErrorCode = ErrorCode.PROTOCOL) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(not 33 <= ord(c) <= 126 for c in value)):
        raise ProviderError(code)
    return value


def finite_number(value: object, *, positive: bool = True) -> float:
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (value <= 0 if positive else value < 0) or value > 1e12):
        raise ProviderError(ErrorCode.PROTOCOL)
    return float(value)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError("nonfinite JSON")


def json_object(data: bytes) -> dict:
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError(ErrorCode.PROTOCOL) from None
    if not isinstance(result, dict):
        raise ProviderError(ErrorCode.PROTOCOL)
    return result


@dataclass(frozen=True, slots=True)
class AuthBinding:
    issuer: str
    client_id: str
    resource: str


@dataclass(frozen=True, slots=True)
class AuthEndpoint:
    issuer: str = AUTH_ISSUER
    local_testing: bool = False

    def __post_init__(self) -> None:
        if self.local_testing:
            p = urlsplit(self.issuer)
            if (p.scheme != "http" or p.hostname not in {"127.0.0.1", "::1"} or p.port is None
                    or p.path or p.query or p.fragment or p.username is not None or p.password is not None):
                raise ValueError("Synthetic authentication requires a literal loopback origin")
        elif self.issuer != AUTH_ISSUER:
            raise ValueError("Authentication is bound to the official issuer")

    @property
    def binding(self) -> AuthBinding:
        if self.local_testing:
            return AuthBinding(self.issuer, "wechat-agent-h2-synthetic", self.issuer + "/synthetic-codex")
        return AuthBinding(AUTH_ISSUER, CODEX_CLIENT_ID, OFFICIAL_BASE)


@dataclass(frozen=True, slots=True)
class DeviceChallenge:
    verification_uri: str
    user_code: str = field(repr=False)
    device_auth_id: str = field(repr=False)
    interval: float
    deadline: float


@dataclass(frozen=True, slots=True)
class TokenSet:
    binding: AuthBinding
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    account_id: str = field(repr=False)
    expires_at: float
    residency: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        bounded_text(self.access_token, maximum=16_384)
        bounded_text(self.refresh_token, maximum=16_384)
        bounded_text(self.account_id, maximum=256)
        finite_number(self.expires_at)
        if self.residency is not None:
            bounded_text(self.residency, maximum=128)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object, binding: AuthBinding) -> "TokenSet":
        if (not isinstance(value, dict)
                or set(value) != {"binding", "access_token", "refresh_token", "account_id", "expires_at", "residency"}
                or value["binding"] != asdict(binding)):
            raise ProviderError(ErrorCode.PROTOCOL)
        return cls(binding=binding, **{k: v for k, v in value.items() if k != "binding"})


def parse_tokens(payload: dict, binding: AuthBinding, now: float, previous: TokenSet | None = None) -> TokenSet:
    if previous is not None and previous.binding != binding:
        raise ProviderError(ErrorCode.INPUT)
    access = bounded_text(payload.get("access_token"), maximum=16_384)
    refresh = bounded_text(payload.get("refresh_token", previous.refresh_token if previous else None), maximum=16_384)
    token_type = payload.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ProviderError(ErrorCode.PROTOCOL)
    try:
        parts = access.split(".")
        if len(parts) != 3:
            raise ValueError("Expected JWT claims for routing")
        claims = json_object(base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError):
        raise ProviderError(ErrorCode.PROTOCOL) from None
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        raise ProviderError(ErrorCode.PROTOCOL)
    account = bounded_text(auth.get("chatgpt_account_id"), maximum=256)
    if previous is not None and account != previous.account_id:
        raise ProviderError(ErrorCode.PROTOCOL)
    expiry = finite_number(claims.get("exp"))
    if "expires_in" in payload:
        expiry = min(expiry, now + finite_number(payload["expires_in"]))
    if expiry <= now:
        raise ProviderError(ErrorCode.AUTH)
    residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    token_set = TokenSet(binding, access, refresh, account, expiry, residency)
    # Apply the same header validation as the eventual production transport.
    CodexSession(access, account, residency)
    return token_set


class AuthState(StrEnum):
    LOGGED_OUT = "logged_out"
    ACTIVE = "active"
    REFRESH_PENDING = "refresh_pending"
    REAUTH_REQUIRED = "reauthentication_required"
    PAUSED = "paused"
    STORAGE_ERROR = "storage_error"
