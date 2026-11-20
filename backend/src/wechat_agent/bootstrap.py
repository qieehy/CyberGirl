"""Application composition; owns shutdown order across Runtime, HTTP and credentials."""

import asyncio
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from .chat import ChatEngine
from .context import ChatSettings
from .mock_backend import MockBackend
from .providers.codex import CodexEndpoint, CodexTextProvider, ErrorCode, ProviderError
from .providers.codex.auth import CodexAuthClient
from .providers.codex.auth_models import AuthEndpoint, DeviceChallenge
from .providers.codex.credentials import CredentialStore
from .providers.codex.token_manager import TokenManager, storage_call

_MOCK_DIRECTORY = Path.cwd() / ".build" / "h3-mock"


class ChatApplication:
    def __init__(self, engine: ChatEngine, provider: CodexTextProvider, tokens: TokenManager,
                 stack: AsyncExitStack, backend: MockBackend | None = None) -> None:
        self.engine, self.provider, self.tokens = engine, provider, tokens
        self.backend = backend
        self._stack = stack
        self._close_task: asyncio.Task | None = None
        self._logout_task: asyncio.Task | None = None

    async def logout(self) -> None:
        if self._close_task is not None:
            raise ProviderError(ErrorCode.CLOSED)
        if self._logout_task is None:
            self._logout_task = asyncio.create_task(self._finish_logout(), name="wechat-application-logout")
        await asyncio.shield(self._logout_task)

    async def _finish_logout(self) -> None:
        # Stop delivery/model work before changing the authenticated identity.
        await self.engine.aclose()
        await self.provider.aclose()
        await self.tokens.logout()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish_close(), name="wechat-application-close")
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        try:
            if self._logout_task is not None:
                await self._logout_task
        finally:
            await self._stack.aclose()


def _temporary_store(directory: Path) -> tempfile.TemporaryDirectory:
    root = directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="h3-", dir=root)
    if not Path(temporary.name).resolve().is_relative_to(root):
        raise ValueError("Temporary store escaped the application scratch directory")
    return temporary


async def _assemble(stack: AsyncExitStack, auth: CodexAuthClient, store: CredentialStore,
                    model: str, endpoint: CodexEndpoint, settings: ChatSettings,
                    backend: MockBackend | None = None) -> ChatApplication:
    tokens = await stack.enter_async_context(TokenManager(store, auth))
    if backend is not None:
        async def accept_synthetic_challenge(challenge: DeviceChallenge) -> None:
            pass

        await tokens.login(accept_synthetic_challenge)
    else:
        tokens.context_key()  # Fail before reading chat input when not logged in.
    provider = await stack.enter_async_context(CodexTextProvider(
        model=model, session_source=tokens.get_session, endpoint=endpoint,
        recover_session=tokens.recover_after_401, invalidate_session=tokens.invalidate_after_401,
    ))
    engine = await stack.enter_async_context(ChatEngine(provider, tokens.context_key, settings))
    return ChatApplication(engine, provider, tokens, stack, backend)


@asynccontextmanager
async def open_mock_application(settings: ChatSettings | None = None, *, scratch: Path | None = None):
    stack = AsyncExitStack()
    app = None
    try:
        backend = await stack.enter_async_context(MockBackend())
        temporary = await storage_call(_temporary_store, scratch or _MOCK_DIRECTORY)
        stack.callback(temporary.cleanup)
        endpoint = AuthEndpoint(backend.origin, local_testing=True)
        store = CredentialStore(Path(temporary.name), endpoint.binding)

        async def skip_synthetic_poll_delay(seconds: float) -> None:
            await asyncio.sleep(0)

        auth = CodexAuthClient(endpoint, sleep=skip_synthetic_poll_delay)
        app = await _assemble(stack, auth, store, "h3-mock-model",
                              CodexEndpoint(backend.origin, local_testing=True), settings or ChatSettings(), backend)
        yield app
    finally:
        if app is not None:
            await app.aclose()
        else:
            await stack.aclose()


@asynccontextmanager
async def open_live_application(model: str, state_directory: Path, settings: ChatSettings | None = None):
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ProviderError(ErrorCode.INPUT)
    stack = AsyncExitStack()
    app = None
    try:
        endpoint = AuthEndpoint()
        store = CredentialStore(state_directory, endpoint.binding)
        auth = CodexAuthClient(endpoint)
        app = await _assemble(stack, auth, store, model, CodexEndpoint(), settings or ChatSettings())
        yield app
    finally:
        if app is not None:
            await app.aclose()
        else:
            await stack.aclose()
