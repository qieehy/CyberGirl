"""Stable, payload-free provider failures. Cancellation retains asyncio semantics."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INPUT = "invalid_input"
    PROTOCOL = "incompatible_protocol"
    INCOMPLETE = "incomplete_response"
    REFUSAL = "refusal"
    LIMIT = "limit_exceeded"
    AUTH = "authentication_required"
    FORBIDDEN = "forbidden"
    REGION = "unsupported_region"
    RATE_LIMIT = "rate_limited"
    HTTP = "http_failure"
    TIMEOUT = "timeout"
    CONNECTION = "connection_failure"
    CLOSED = "closed"
    BUSY = "busy"
    STORAGE = "credential_storage_failure"
    DEVICE_EXPIRED = "device_code_expired"
    DELIVERY = "delivery_unconfirmed"


class ProviderError(Exception):
    """Only safe codes and bounded HTTP metadata; never remote bodies or input text."""

    def __init__(
        self, code: ErrorCode, *, status: int | None = None,
        request_id: str | None = None, retry_after: float | None = None,
    ) -> None:
        super().__init__(f"Codex provider: {code.value}")
        self.code = code
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after


def safe_request_id(value: str | None) -> str | None:
    if value and len(value) <= 128 and all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        return value
    return None
