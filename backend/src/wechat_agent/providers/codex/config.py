"""Explicit resource bounds and credential destinations."""

import math
from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit

OFFICIAL_BASE = "https://chatgpt.com/backend-api/codex"


@dataclass(frozen=True, slots=True)
class Limits:
    messages: int = 64
    input_chars: int = 100_000
    output_chars: int = 10_000
    delta_chars: int = 20_000
    output_items: int = 32
    events: int = 512
    line_bytes: int = 256 * 1024
    event_bytes: int = 512 * 1024
    stream_bytes: int = 2 * 1024 * 1024
    pending_calls: int = 4

    def __post_init__(self) -> None:
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) <= 0 for f in fields(self)):
            raise ValueError("Limits must be positive integers")


@dataclass(frozen=True, slots=True)
class Timeouts:
    connect: float = 10.0
    read: float = 30.0
    write: float = 10.0
    pool: float = 5.0
    total: float = 120.0

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite positive numbers")


@dataclass(frozen=True, slots=True)
class CodexEndpoint:
    base_url: str = OFFICIAL_BASE
    local_testing: bool = False

    def __post_init__(self) -> None:
        if self.local_testing:
            p = urlsplit(self.base_url)
            if (p.scheme != "http" or p.hostname not in {"127.0.0.1", "::1"}
                    or p.username is not None or p.password is not None
                    or p.query or p.fragment or p.path or p.port is None):
                raise ValueError("Tests require a literal loopback HTTP origin with an explicit port")
        elif self.base_url != OFFICIAL_BASE:
            raise ValueError("Codex credentials are bound to the official endpoint")

    @property
    def responses_url(self) -> str:
        return self.base_url + "/responses"


@dataclass(frozen=True, slots=True)
class CodexSession:
    """H2 supplies this value. It is never accepted by a local test endpoint."""

    access_token: str = field(repr=False)
    account_id: str = field(repr=False)
    residency: str | None = field(default=None, repr=False)
    context_key: str | None = field(default=None, repr=False)
    revision: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        for value, maximum in [(self.access_token, 16_384), (self.account_id, 256)]:
            if (not isinstance(value, str) or not value or len(value) > maximum
                    or any(not 33 <= ord(c) <= 126 for c in value)):
                raise ValueError("Invalid credential header")
        if self.residency is not None:
            if (not isinstance(self.residency, str) or not self.residency or len(self.residency) > 128
                    or any(not 33 <= ord(c) <= 126 for c in self.residency)):
                raise ValueError("Invalid residency header")
        if self.context_key is not None and (not isinstance(self.context_key, str) or not self.context_key or len(self.context_key) > 128):
            raise ValueError("Invalid session context key")


@dataclass(frozen=True, slots=True)
class SyntheticSession:
    """Unchangeable fake credentials, only valid for explicit loopback tests."""

    access_token: str = field(default="h1-synthetic-token", init=False, repr=False)
    account_id: str = field(default="h1-synthetic-account", init=False, repr=False)
    context_key: str | None = field(default=None, repr=False)
    revision: str = ""
