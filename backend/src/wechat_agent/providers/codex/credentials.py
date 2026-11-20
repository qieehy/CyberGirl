"""A versioned, app-bound DPAPI vault with atomic replacement and exclusive ownership."""

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .auth_models import AuthBinding, AuthState, TokenSet, finite_number, json_object
from .errors import ErrorCode, ProviderError
from .protection import WindowsOwner, WindowsProtection


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    binding: AuthBinding
    generation: str
    state: AuthState
    tokens: TokenSet | None = field(default=None, repr=False)
    retry_at: float | None = None
    last_error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.generation, str) or len(self.generation) != 32
                or any(c not in "0123456789abcdef" for c in self.generation)):
            raise ProviderError(ErrorCode.PROTOCOL)
        if not isinstance(self.state, AuthState):
            raise ProviderError(ErrorCode.PROTOCOL)
        if self.state in {AuthState.ACTIVE, AuthState.PAUSED}:
            if not isinstance(self.tokens, TokenSet) or self.tokens.binding != self.binding:
                raise ProviderError(ErrorCode.PROTOCOL)
        elif self.tokens is not None:
            raise ProviderError(ErrorCode.PROTOCOL)
        if self.state == AuthState.PAUSED:
            finite_number(self.retry_at)
        elif self.retry_at is not None:
            raise ProviderError(ErrorCode.PROTOCOL)
        if self.last_error is not None and not isinstance(self.last_error, ErrorCode):
            raise ProviderError(ErrorCode.PROTOCOL)

    @classmethod
    def empty(cls, binding: AuthBinding) -> "CredentialRecord":
        return cls(binding, uuid.uuid4().hex, AuthState.LOGGED_OUT)

    def to_bytes(self) -> bytes:
        value = {"version": 1, "app": "wechat-agent", "binding": asdict(self.binding),
                 "generation": self.generation, "state": self.state.value,
                 "tokens": self.tokens.to_dict() if self.tokens else None,
                 "retry_at": self.retry_at, "last_error": self.last_error.value if self.last_error else None}
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes, binding: AuthBinding) -> "CredentialRecord":
        value = json_object(data)
        if (set(value) != {"version", "app", "binding", "generation", "state", "tokens", "retry_at", "last_error"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["app"] != "wechat-agent" or value["binding"] != asdict(binding)):
            raise ProviderError(ErrorCode.PROTOCOL)
        try:
            tokens = TokenSet.from_dict(value["tokens"], binding) if value["tokens"] is not None else None
            return cls(binding, value["generation"], AuthState(value["state"]), tokens,
                       value["retry_at"], ErrorCode(value["last_error"]) if value["last_error"] is not None else None)
        except (ValueError, TypeError):
            raise ProviderError(ErrorCode.PROTOCOL) from None


class CredentialStore:
    """Synchronous bounded local I/O; TokenManager runs it in owned worker tasks.

    The lease spans the entire manager lifetime, including any network refresh.
    A corrupt vault is never overwritten by load(). Explicit logout may reset it.
    """

    def __init__(self, directory: Path, binding: AuthBinding) -> None:
        self.directory = directory.resolve()
        self.path = self.directory / "codex-auth.dpapi"
        self.binding = binding
        self._owner = WindowsOwner()
        self._protection = WindowsProtection()

    def acquire(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._owner.acquire(self.directory / "owner.lock")
        except OSError:
            raise ProviderError(ErrorCode.STORAGE) from None

    def _require_owner(self) -> None:
        if not self._owner.acquired:
            raise ProviderError(ErrorCode.STORAGE)

    def load(self) -> CredentialRecord:
        self._require_owner()
        try:
            if not self.path.exists():
                return CredentialRecord.empty(self.binding)
            with self.path.open("rb") as stream:
                encrypted = stream.read(256 * 1024 + 1)
            if len(encrypted) > 256 * 1024:
                raise ProviderError(ErrorCode.STORAGE)
            return CredentialRecord.from_bytes(self._protection.unprotect(encrypted), self.binding)
        except (OSError, ProviderError):
            raise ProviderError(ErrorCode.STORAGE) from None

    def save(self, record: CredentialRecord) -> None:
        self._require_owner()
        if record.binding != self.binding:
            raise ProviderError(ErrorCode.STORAGE)
        temporary = None
        try:
            plaintext = record.to_bytes()
            if len(plaintext) > 128 * 1024:
                raise ProviderError(ErrorCode.STORAGE)
            encrypted = self._protection.protect(plaintext)
            fd, filename = tempfile.mkstemp(prefix=".codex-auth-", suffix=".tmp", dir=self.directory)
            temporary = Path(filename)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, ProviderError):
            raise ProviderError(ErrorCode.STORAGE) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def reset(self) -> None:
        """Explicit local logout, including a corrupt vault; contains no tokens."""
        self.save(CredentialRecord.empty(self.binding))

    def close(self) -> None:
        self._owner.close()
