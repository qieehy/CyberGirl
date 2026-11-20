"""Strict text-only proposals and program-bound delivery targets."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum

from ..context import ConversationScope


class SocialError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise SocialError("invalid_identity")


def text_value(value: str, maximum: int, *, empty: bool = False) -> None:
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in "\n\r\t" for c in value)):
        raise SocialError("invalid_text")


def timestamp(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e12:
        raise SocialError("invalid_time")


@dataclass(frozen=True)
class IncomingEvent:
    scope: ConversationScope
    sender_id: str
    message_id: str
    occurred_at: float
    received_at: float
    text: str = field(repr=False)
    source_verified: bool = False
    is_self: bool = False
    mention_self: bool = False
    reply_to: str | None = None

    def __post_init__(self):
        if not isinstance(self.scope, ConversationScope):
            raise SocialError("invalid_scope")
        identifier(self.sender_id)
        identifier(self.message_id)
        timestamp(self.occurred_at)
        timestamp(self.received_at)
        text_value(self.text, 4000)
        if self.reply_to is not None:
            identifier(self.reply_to)
        if any(type(v) is not bool for v in (self.source_verified, self.is_self, self.mention_self)):
            raise SocialError("invalid_event")

    @property
    def key(self) -> str:
        return hashlib.sha256(json.dumps([self.scope.key(), self.message_id]).encode()).hexdigest()

    @property
    def fingerprint(self) -> str:
        # A later delivery timestamp does not make a duplicate a different event.
        values = [self.sender_id, self.text, self.occurred_at, self.source_verified, self.is_self, self.mention_self]
        if self.reply_to is not None:
            values.append(self.reply_to)
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


class ActionKind(StrEnum):
    SILENT = "SILENT"
    REPLY = "REPLY"


@dataclass(frozen=True)
class Decision:
    action: ActionKind
    reply_text: str = field(default="", repr=False)

    def __post_init__(self):
        if not isinstance(self.action, ActionKind):
            raise SocialError("invalid_decision")
        text_value(self.reply_text, 2000, empty=self.action == ActionKind.SILENT)
        if self.action == ActionKind.SILENT and self.reply_text:
            raise SocialError("invalid_decision")

    @classmethod
    def parse(cls, value: str):
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = item
            return result
        try:
            data = json.loads(value, object_pairs_hook=unique)
            if not isinstance(data, dict) or set(data) != {"action", "reply_text"}:
                raise ValueError("shape")
            return cls(ActionKind(data["action"]), data["reply_text"])
        except (ValueError, TypeError, RecursionError):
            raise SocialError("invalid_decision") from None


@dataclass(frozen=True)
class OutgoingAction:
    action_id: str
    scope: ConversationScope
    reply_to: str
    text: str = field(repr=False)


class ReceiptStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    NOT_SUBMITTED = "NOT_SUBMITTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Receipt:
    action_id: str
    scope: ConversationScope
    status: ReceiptStatus
    receipt_id: str | None = None

    def __post_init__(self):
        identifier(self.action_id)
        if not isinstance(self.scope, ConversationScope) or not isinstance(self.status, ReceiptStatus):
            raise SocialError("invalid_receipt")
        if self.receipt_id is not None:
            identifier(self.receipt_id)
        if self.status == ReceiptStatus.CONFIRMED and self.receipt_id is None:
            raise SocialError("invalid_receipt")
