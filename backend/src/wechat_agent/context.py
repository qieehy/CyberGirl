"""Bounded, acknowledged text history and a disposable per-turn framework memory adapter."""

import json
from dataclasses import dataclass, field

from .framework import BufferMemory, FrameworkMemoryError, MemoryManager
from .providers.codex.errors import ErrorCode, ProviderError


@dataclass(frozen=True, slots=True)
class ConversationScope:
    platform: str
    account_id: str
    conversation_type: str
    conversation_id: str

    def __post_init__(self) -> None:
        for value in (self.platform, self.account_id, self.conversation_type, self.conversation_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ProviderError(ErrorCode.INPUT)

    def key(self) -> str:
        return json.dumps([self.platform, self.account_id, self.conversation_type, self.conversation_id],
                          ensure_ascii=False, separators=(",", ":"))


LOCAL_SCOPE = ConversationScope("console", "owner", "dm", "local")


@dataclass(frozen=True, slots=True)
class ConfirmedPair:
    user: str = field(repr=False)
    assistant: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChatSettings:
    system_prompt: str = field(default="你是透明的 AI 助手。使用自然、简洁的中文回应，遵守当前会话的上下文。", repr=False)
    max_input_chars: int = 8000
    max_output_chars: int = 10_000
    context_chars: int = 48_000
    history_pairs: int = 16
    sessions: int = 64
    pending_turns: int = 4
    turn_timeout: float = 120.0

    def __post_init__(self) -> None:
        import math

        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip() or len(self.system_prompt) > 8000:
            raise ValueError("A bounded trusted prompt is required")
        for value in (self.max_input_chars, self.max_output_chars, self.context_chars,
                      self.history_pairs, self.sessions, self.pending_turns):
            if type(value) is not int or value <= 0:
                raise ValueError("Chat bounds must be positive integers")
        if (self.history_pairs > 30 or self.context_chars > 100_000 or self.max_output_chars > 10_000
                or self.context_chars < len(self.system_prompt) + self.max_input_chars
                or type(self.turn_timeout) not in (int, float) or not math.isfinite(self.turn_timeout)
                or self.turn_timeout <= 0):
            raise ValueError("Chat bounds exceed the text provider contract")


def select_history(history: tuple[ConfirmedPair, ...], user: str, settings: ChatSettings) -> tuple[ConfirmedPair, ...]:
    budget = settings.context_chars - len(settings.system_prompt) - len(user)
    selected = []
    for pair in reversed(history[-settings.history_pairs:]):
        size = len(pair.user) + len(pair.assistant)
        if size > budget:
            break
        selected.append(pair)
        budget -= size
    return tuple(reversed(selected))


class TurnBuffer(BufferMemory):
    """Public BufferMemory contract restricted to one bounded text-only run.

    Retention is decided by select_history, so the framework cannot trim away
    trusted instructions or split a confirmed pair. UTF-8 size is a conservative
    memory estimate, not reported model usage. Literal tokenizer special strings
    remain ordinary text; no private framework state or global tokenizer is patched.
    """

    def __init__(self, system: str, history: tuple[ConfirmedPair, ...], max_chars: int) -> None:
        self._trusted = {"role": "system", "content": system}
        self._text_messages: list[dict[str, str]] = []
        self._max_chars = max_chars
        for pair in history:
            self.add_message({"role": "user", "content": pair.user})
            self.add_message({"role": "assistant", "content": pair.assistant})

    @property
    def messages(self) -> list[dict]:
        return [dict(message) for message in self._text_messages]

    @property
    def total_tokens(self) -> int:
        return sum(len(message["content"].encode("utf-8")) + 8 for message in self.get_context())

    def add_message(self, message: dict) -> None:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") not in ("user", "assistant") or not isinstance(message.get("content"), str)):
            raise FrameworkMemoryError("Text turn memory rejected message shape")
        size = len(self._trusted["content"]) + sum(len(m["content"]) for m in self._text_messages) + len(message["content"])
        if size > self._max_chars:
            raise FrameworkMemoryError("Text turn memory limit exceeded")
        self._text_messages.append(dict(message))

    def get_context(self) -> list[dict]:
        return [dict(self._trusted), *self.messages]

    def get_system_message(self) -> dict:
        return dict(self._trusted)

    def clear(self) -> None:
        self._text_messages.clear()


class TurnMemoryManager(MemoryManager):
    """Single-scope disposable adapter; drafts are never persisted by Runtime."""

    def __init__(self, session_id: str, buffer: TurnBuffer) -> None:
        self.session_id, self.buffer = session_id, buffer

    def get_or_create(self, session_id: str, system_message: dict | None = None) -> TurnBuffer:
        if session_id != self.session_id or system_message is not None:
            raise FrameworkMemoryError("Text turn memory scope mismatch")
        return self.buffer

    def save(self, session_id: str) -> None:
        raise FrameworkMemoryError("Use application delivery confirmation to retain a turn")

    def load(self, session_id: str) -> TurnBuffer | None:
        return self.buffer if session_id == self.session_id else None

    def delete_session(self, session_id: str) -> None:
        self.get_or_create(session_id).clear()

    def list_sessions(self) -> list[str]:
        return [self.session_id]
