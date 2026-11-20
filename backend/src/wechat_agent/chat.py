"""One actual framework run per turn; history only advances after delivery acknowledgement."""

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from llm.models import LLMResult, TokenUsage

from .context import (
    ChatSettings,
    ConfirmedPair,
    ConversationScope,
    TurnBuffer,
    TurnMemoryManager,
    select_history,
)
from .framework import (
    Event,
    Executor,
    LLMError,
    LoopGuard,
    LoopPolicy,
    RunStatus,
    Runtime,
    SessionCoordinator,
    ToolCallValidator,
    ToolRegistry,
    ToolRoute,
    ToolRoutingError,
)
from .providers.codex.errors import ErrorCode, ProviderError


class TextModel(Protocol):
    async def __call__(self, messages: list[dict], tools: list[dict] | None = None, *,
                       context_key: str | None = None) -> LLMResult: ...


class NoToolsRouter:
    async def route(self, query: str, schemas: list[dict]) -> ToolRoute:
        if schemas:
            raise ToolRoutingError("Text application must not register tools")
        return ToolRoute((), (), "text-only:no-embeddings", 1.0)


@dataclass(frozen=True, slots=True)
class Draft:
    text: str = field(repr=False)
    run_id: str
    model: str | None
    usage: TokenUsage | None
    request_id: str | None


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    draft: Draft
    confirmed: bool
    runtime_class: str
    status: str
    steps: int
    model_calls: int
    events: tuple[str, ...]


@dataclass(slots=True)
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    history: tuple[ConfirmedPair, ...] = ()
    references: int = 0


Delivery = Callable[[Draft], Awaitable[bool]]


class ChatEngine:
    """Owns turn tasks and bounded RAM history; bootstrap owns model/auth resources."""

    def __init__(self, model: TextModel, context_key: Callable[[], str], settings: ChatSettings | None = None) -> None:
        self._model, self._identity = model, context_key
        self.settings = settings or ChatSettings()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()
        self._epoch: str | None = None
        self._closed = False
        self._close_task: asyncio.Task | None = None

    def _scope_key(self, scope: ConversationScope, epoch: str) -> str:
        return hashlib.sha256((epoch + "\0" + scope.key()).encode()).hexdigest()

    def _reserve(self, scope: ConversationScope, epoch: str) -> tuple[str, _Entry]:
        if self._epoch != epoch:
            self._entries.clear()
            self._epoch = epoch
        key = self._scope_key(scope, epoch)
        if key not in self._entries:
            if len(self._entries) >= self.settings.sessions:
                victim = next((k for k, entry in self._entries.items() if entry.references == 0), None)
                if victim is None:
                    raise ProviderError(ErrorCode.BUSY)
                del self._entries[victim]
            self._entries[key] = _Entry()
        entry = self._entries[key]
        self._entries.move_to_end(key)
        entry.references += 1
        return key, entry

    async def turn(self, scope: ConversationScope, text: str, deliver: Delivery) -> TurnOutcome:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        if (not isinstance(scope, ConversationScope) or not isinstance(text, str) or not text.strip()
                or len(text) > self.settings.max_input_chars or not callable(deliver)):
            raise ProviderError(ErrorCode.INPUT)
        if len(self._tasks) >= self.settings.pending_turns:
            raise ProviderError(ErrorCode.BUSY)
        epoch = self._identity()
        key, entry = self._reserve(scope, epoch)
        expires_at = asyncio.get_running_loop().time() + self.settings.turn_timeout
        task = asyncio.create_task(self._turn(key, entry, epoch, text, deliver, expires_at), name="wechat-chat-turn")
        self._tasks.add(task)
        try:
            return await task
        finally:
            entry.references -= 1
            self._tasks.discard(task)

    def _check_epoch(self, expected: str) -> None:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        if self._identity() != expected:
            raise ProviderError(ErrorCode.AUTH)

    async def _turn(self, key: str, entry: _Entry, epoch: str, text: str,
                    deliver: Delivery, expires_at: float) -> TurnOutcome:
        try:
            async with asyncio.timeout_at(expires_at):
                async with entry.lock:
                    self._check_epoch(epoch)
                    history = select_history(entry.history, text, self.settings)
                    buffer = TurnBuffer(self.settings.system_prompt, history,
                                        self.settings.context_chars + self.settings.max_output_chars)
                    memory = TurnMemoryManager(key, buffer)
                    registry = ToolRegistry()
                    event_types: list[str] = []
                    result: LLMResult | None = None
                    provider_error: ProviderError | None = None

                    async def call_model(messages: list[dict], tools: list[dict] | None) -> LLMResult:
                        nonlocal result, provider_error
                        self._check_epoch(epoch)
                        try:
                            value = await self._model(messages, tools, context_key=epoch)
                            if (not isinstance(value, LLMResult) or value.message.tool_calls or value.message.refusal
                                    or not isinstance(value.message.content, str) or not value.message.content.strip()
                                    or len(value.message.content) > self.settings.max_output_chars):
                                raise ProviderError(ErrorCode.PROTOCOL)
                        except ProviderError as exc:
                            provider_error = exc
                            raise LLMError("Text provider failed", context={"code": exc.code.value}) from None
                        except Exception:
                            provider_error = ProviderError(ErrorCode.PROTOCOL)
                            raise LLMError("Text provider returned an incompatible result") from None
                        result = value
                        return value

                    def on_event(event: Event) -> None:
                        # Runtime events can contain full text/results. Keep only
                        # known event names; never forward payloads to logs/UI.
                        if len(event_types) < 32:
                            event_types.append(event.type.value)

                    runtime = Runtime(
                        tool_executor=Executor(registry, mode="serial", max_workers=1),
                        memory_manager=memory, llm_call_async=call_model,
                        loop_guard=LoopGuard(LoopPolicy(max_steps=1, validation_feedback_rounds=0,
                                                       tool_error_feedback_rounds=0, reflection_revision_rounds=0)),
                        validator=ToolCallValidator([]), tool_router=NoToolsRouter(),
                        handlers=[on_event], system_prompt=None,
                        session_coordinator=SessionCoordinator(), planner=None, critic=None,
                    )
                    try:
                        state = await runtime.run_async(text, session_id=key)
                        if state.status != RunStatus.FINISHED or result is None:
                            raise provider_error or ProviderError(ErrorCode.PROTOCOL)
                        self._check_epoch(epoch)
                        draft = Draft(result.message.content, state.run_id, result.model, result.usage, result.request_id)
                        try:
                            acknowledged = await deliver(draft)
                        except Exception:
                            raise ProviderError(ErrorCode.DELIVERY) from None
                        if type(acknowledged) is not bool:
                            raise ProviderError(ErrorCode.PROTOCOL)
                        self._check_epoch(epoch)
                        if asyncio.get_running_loop().time() >= expires_at:
                            raise ProviderError(ErrorCode.TIMEOUT)
                        if acknowledged:
                            entry.history = tuple((*history, ConfirmedPair(text, draft.text))[-self.settings.history_pairs:])
                        return TurnOutcome(draft, acknowledged, type(runtime).__module__ + "." + type(runtime).__name__,
                                           state.status.value, state.step_count, state.usage.calls, tuple(event_types))
                    finally:
                        buffer.clear()
        except TimeoutError:
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(ErrorCode.PROTOCOL) from None

    def history(self, scope: ConversationScope) -> tuple[ConfirmedPair, ...]:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        epoch = self._identity()
        entry = self._entries.get(self._scope_key(scope, epoch))
        return entry.history if entry else ()

    async def reset(self, scope: ConversationScope) -> None:
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        epoch = self._identity()
        entry = self._entries.get(self._scope_key(scope, epoch))
        if entry is not None:
            if entry.references:
                raise ProviderError(ErrorCode.BUSY)
            entry.history = ()

    async def __aenter__(self) -> "ChatEngine":
        if self._closed:
            raise ProviderError(ErrorCode.CLOSED)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            pending = list(self._tasks)
            self._close_task = asyncio.create_task(self._finish_close(pending), name="wechat-chat-close")
        await asyncio.shield(self._close_task)

    async def _finish_close(self, pending: list[asyncio.Task]) -> None:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._entries.clear()
