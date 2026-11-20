"""One refresh owner, durable rotation fencing, and logout/login generation checks."""

import asyncio
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Protocol, TypeVar

from .auth_models import AuthBinding, AuthState, DeviceChallenge, TokenSet, finite_number
from .config import CodexSession, SyntheticSession
from .credentials import CredentialRecord, CredentialStore
from .errors import ErrorCode, ProviderError

_T = TypeVar("_T")
ChallengeHandler = Callable[[DeviceChallenge], Awaitable[None]]


class AuthClient(Protocol):
    binding: AuthBinding

    async def begin_device_login(self) -> DeviceChallenge: ...
    async def complete_device_login(self, challenge: DeviceChallenge) -> TokenSet: ...
    async def refresh(self, previous: TokenSet) -> TokenSet: ...
    async def aclose(self) -> None: ...


async def storage_call(function: Callable[..., _T], *args: object) -> _T:
    """Finish native I/O before propagating cancellation; never release its lease early."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        task.result()
        raise asyncio.CancelledError
    return task.result()


def _consume_failure(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class TokenManager:
    """Use async with. Owns store lease and auth client; inference provider owns neither."""

    def __init__(
        self, store: CredentialStore, auth: AuthClient, *, now: Callable[[], float] = time.time,
        refresh_skew: float = 120,
    ) -> None:
        if store.binding != auth.binding:
            raise ValueError("Credential store and auth route must match")
        self._store, self._auth, self._now = store, auth, now
        self._skew = finite_number(refresh_skew, positive=False)
        self._record = CredentialRecord.empty(auth.binding)
        self._lock = asyncio.Lock()
        self._opened = False
        self._started = False
        self._closed = False
        self._closing = False
        self._generation = self._record.generation
        self._login_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._owns_store = False

    def _acquire_store(self) -> None:
        self._store.acquire()
        self._owns_store = True

    def _release_store(self) -> None:
        if self._owns_store:
            self._store.close()
            self._owns_store = False

    async def __aenter__(self) -> "TokenManager":
        if self._started:
            raise ProviderError(ErrorCode.CLOSED)
        self._started = True
        try:
            await storage_call(self._acquire_store)
            self._record = await storage_call(self._store.load)
            self._generation = self._record.generation
            if self._record.state == AuthState.REFRESH_PENDING:
                self._record = replace(self._record, state=AuthState.REAUTH_REQUIRED, last_error=ErrorCode.AUTH)
                await storage_call(self._store.save, self._record)
            self._opened = True
            return self
        except BaseException:
            await storage_call(self._release_store)
            await self._auth.aclose()
            self._closed = True
            raise

    def _check_open(self) -> None:
        if not self._opened or self._closed or self._closing:
            raise ProviderError(ErrorCode.CLOSED)

    async def _save(self, record: CredentialRecord) -> None:
        try:
            await storage_call(self._store.save, record)
        except asyncio.CancelledError:
            # storage_call only propagates cancellation after a successful write.
            self._record = record
            raise
        except ProviderError:
            self._record = CredentialRecord(self._auth.binding, self._generation, AuthState.STORAGE_ERROR,
                                            last_error=ErrorCode.STORAGE)
            raise
        self._record = record

    def status(self) -> dict[str, object]:
        self._check_open()
        record = self._record
        state = record.state.value
        if self._login_task is not None and not self._login_task.done():
            state = "waiting_for_login"
        elif state == AuthState.ACTIVE and record.tokens is not None:
            state = "ready" if record.tokens.expires_at > self._now() + self._skew else "refresh_required"
        return {"state": state, "expires_at": record.tokens.expires_at if record.tokens else None,
                "account_fingerprint": hashlib.sha256(record.tokens.account_id.encode()).hexdigest()[:12] if record.tokens else None,
                "retry_at": record.retry_at, "last_error": record.last_error.value if record.last_error else None}

    def context_key(self) -> str:
        """Opaque login epoch; stable across refresh, changes after login/logout."""
        self._check_open()
        if self._record.state == AuthState.STORAGE_ERROR:
            raise ProviderError(ErrorCode.STORAGE)
        if self._record.state not in {AuthState.ACTIVE, AuthState.PAUSED, AuthState.REFRESH_PENDING}:
            raise ProviderError(ErrorCode.AUTH)
        return hashlib.sha256((self._auth.binding.issuer + "\0" + self._generation).encode()).hexdigest()

    def _revision(self, record: CredentialRecord) -> str:
        if record.tokens is None:
            raise ProviderError(ErrorCode.AUTH)
        pair = (record.generation, record.tokens.access_token, record.tokens.refresh_token,
                str(record.tokens.expires_at), record.tokens.residency or "")
        return hashlib.sha256("\0".join(pair).encode()).hexdigest()

    def _matches_session(self, session: CodexSession | SyntheticSession, record: CredentialRecord) -> bool:
        if record.tokens is None:
            return False
        if type(session) is SyntheticSession and self._auth.binding.issuer != "https://auth.openai.com":
            return session.revision == self._revision(record)
        return (type(session) is CodexSession and session.account_id == record.tokens.account_id
                and session.access_token == record.tokens.access_token
                and session.revision == self._revision(record))

    async def recover_after_401(self, rejected: CodexSession | SyntheticSession) -> None:
        """Refresh once for the rejected snapshot; never refresh a newer login/token."""
        async with self._lock:
            if rejected.context_key != self.context_key():
                raise ProviderError(ErrorCode.AUTH)
            if self._refresh_task is not None and not self._refresh_task.done():
                task = self._refresh_task
            else:
                record = self._record
                if record.state == AuthState.PAUSED and record.retry_at > self._now():
                    raise ProviderError(ErrorCode.RATE_LIMIT, retry_after=record.retry_at - self._now())
                if not self._matches_session(rejected, record):
                    return
                task = asyncio.create_task(self._refresh(record), name="codex-refresh-after-401")
                task.add_done_callback(_consume_failure)
                self._refresh_task = task
        await asyncio.shield(task)

    async def invalidate_after_401(self, rejected: CodexSession | SyntheticSession) -> None:
        """Quarantine a twice-rejected current snapshot, leaving newer sessions alone."""
        async with self._lock:
            self._check_open()
            if rejected.context_key != self.context_key() or not self._matches_session(rejected, self._record):
                return
            await self._save(CredentialRecord(self._auth.binding, self._generation,
                                             AuthState.REAUTH_REQUIRED, last_error=ErrorCode.AUTH))

    async def login(self, on_challenge: ChallengeHandler) -> None:
        async with self._lock:
            self._check_open()
            if ((self._login_task is not None and not self._login_task.done())
                    or (self._refresh_task is not None and not self._refresh_task.done())):
                raise ProviderError(ErrorCode.BUSY)
            self._generation = uuid.uuid4().hex
            await self._save(CredentialRecord(self._auth.binding, self._generation, AuthState.LOGGED_OUT))
            task = asyncio.create_task(self._login(self._generation, on_challenge), name="codex-device-login")
            task.add_done_callback(_consume_failure)
            self._login_task = task
        await task

    async def _login(self, generation: str, on_challenge: ChallengeHandler) -> None:
        try:
            challenge = await self._auth.begin_device_login()
            await on_challenge(challenge)
            tokens = await self._auth.complete_device_login(challenge)
            async with self._lock:
                if self._closed or self._closing or generation != self._generation:
                    raise ProviderError(ErrorCode.AUTH)
                if tokens.binding != self._auth.binding or tokens.expires_at <= self._now() + self._skew:
                    raise ProviderError(ErrorCode.PROTOCOL)
                await self._save(CredentialRecord(tokens.binding, generation, AuthState.ACTIVE, tokens))
        except asyncio.CancelledError:
            async with self._lock:
                if not self._closed and generation == self._generation:
                    await self._save(CredentialRecord(self._auth.binding, generation, AuthState.LOGGED_OUT))
            raise

    async def get_session(self) -> CodexSession | SyntheticSession:
        while True:
            async with self._lock:
                self._check_open()
                if self._record.state == AuthState.STORAGE_ERROR:
                    raise ProviderError(ErrorCode.STORAGE)
                if self._record.state in {AuthState.LOGGED_OUT, AuthState.REAUTH_REQUIRED}:
                    raise ProviderError(ErrorCode.AUTH)
                if self._refresh_task is not None and not self._refresh_task.done():
                    task = self._refresh_task
                else:
                    record = self._record
                    if record.state == AuthState.STORAGE_ERROR:
                        raise ProviderError(ErrorCode.STORAGE)
                    if record.state == AuthState.PAUSED and record.retry_at > self._now():
                        raise ProviderError(ErrorCode.RATE_LIMIT, retry_after=record.retry_at - self._now())
                    if record.state not in {AuthState.ACTIVE, AuthState.PAUSED} or record.tokens is None:
                        raise ProviderError(ErrorCode.AUTH)
                    if record.tokens.expires_at > self._now() + self._skew:
                        if record.state == AuthState.PAUSED:
                            await self._save(replace(record, state=AuthState.ACTIVE, retry_at=None, last_error=None))
                        if self._auth.binding.issuer != "https://auth.openai.com":
                            return SyntheticSession(context_key=self.context_key(), revision=self._revision(record))
                        return CodexSession(record.tokens.access_token, record.tokens.account_id,
                                            record.tokens.residency, self.context_key(), self._revision(record))
                    task = asyncio.create_task(self._refresh(record), name="codex-refresh")
                    task.add_done_callback(_consume_failure)
                    self._refresh_task = task
            # One caller timing out must not discard a rotated refresh token.
            await asyncio.shield(task)

    async def _refresh(self, previous: CredentialRecord) -> None:
        async with self._lock:
            if self._closed or self._generation != previous.generation:
                raise ProviderError(ErrorCode.AUTH)
            # No old token is retained in the durable pending marker. After a
            # process crash, startup demands login instead of replaying a grant.
            await self._save(CredentialRecord(previous.binding, previous.generation, AuthState.REFRESH_PENDING))
        try:
            tokens = await self._auth.refresh(previous.tokens)
            if tokens.binding != previous.binding or tokens.account_id != previous.tokens.account_id:
                raise ProviderError(ErrorCode.PROTOCOL)
            if tokens.expires_at <= self._now() + self._skew:
                raise ProviderError(ErrorCode.PROTOCOL)
        except ProviderError as exc:
            async with self._lock:
                if not self._closed and self._generation == previous.generation:
                    if exc.code == ErrorCode.RATE_LIMIT:
                        await self._save(replace(previous, state=AuthState.PAUSED,
                                                 retry_at=self._now() + (exc.retry_after or 60),
                                                 last_error=ErrorCode.RATE_LIMIT))
                    else:
                        await self._save(CredentialRecord(previous.binding, previous.generation,
                                                         AuthState.REAUTH_REQUIRED, last_error=exc.code))
            raise
        async with self._lock:
            if self._closed or self._generation != previous.generation:
                raise ProviderError(ErrorCode.AUTH)
            await self._save(CredentialRecord(previous.binding, previous.generation, AuthState.ACTIVE, tokens))

    async def logout(self) -> None:
        task = None
        try:
            async with self._lock:
                self._check_open()
                self._generation = uuid.uuid4().hex
                task = self._login_task
                await self._save(CredentialRecord(self._auth.binding, self._generation, AuthState.LOGGED_OUT))
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        # In-flight refresh is allowed to finish its bounded HTTP transaction.
        # Its generation can no longer commit or restore credentials.

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._finish_close(), name="codex-auth-close")
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        if self._login_task is not None and not self._login_task.done():
            self._login_task.cancel()
            await asyncio.gather(self._login_task, return_exceptions=True)
        try:
            if self._refresh_task is not None:
                await asyncio.gather(self._refresh_task, return_exceptions=True)
            await self._auth.aclose()
        finally:
            await storage_call(self._release_store)
            self._closed = True
