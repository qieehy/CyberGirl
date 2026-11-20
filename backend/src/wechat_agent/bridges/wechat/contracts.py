"""Trusted bridge facts, distinct from model/UI input and display names.

These contracts validate consistency, not authenticity. A future native reader
must obtain account/target evidence before constructing bindings.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from ...context import ConversationScope
from ...social.models import (
    IncomingEvent,
    OutgoingAction,
    SocialError,
    identifier,
    text_value,
    timestamp,
)

FILE_HELPER = "filehelper"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise SocialError("invalid_source_integer")


@dataclass(frozen=True)
class WindowBinding:
    pid: int
    created_filetime: int
    hwnd: int
    exe_sha256: str
    dll_sha256: str

    def __post_init__(self):
        for value in (self.pid, self.created_filetime, self.hwnd):
            integer(value, minimum=1)
        for value in (self.exe_sha256, self.dll_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise SocialError("invalid_binary_fingerprint")


@dataclass(frozen=True)
class AccountBinding:
    account_id: str
    db_epoch: str
    self_id: str
    window: WindowBinding
    evidence_ref: str

    def __post_init__(self):
        for value in (self.account_id, self.db_epoch, self.self_id, self.evidence_ref):
            identifier(value)
        if not isinstance(self.window, WindowBinding):
            raise SocialError("invalid_window_binding")

    @property
    def generation(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class ConversationBinding:
    account: AccountBinding
    username: str
    kind: str
    evidence_ref: str

    def __post_init__(self):
        if not isinstance(self.account, AccountBinding) or self.kind not in {"dm", "group"}:
            raise SocialError("invalid_conversation_binding")
        identifier(self.username)
        identifier(self.evidence_ref)

    @property
    def scope(self):
        return ConversationScope("wechat", self.account.account_id, self.kind, self.username)

    @property
    def generation(self):
        return digest(asdict(self))

    def source_key(self, shard):
        identifier(shard)
        return digest([self.scope.key(), self.account.db_epoch, shard])


@dataclass(frozen=True, order=True)
class Cursor:
    sort_seq: int
    local_id: int

    def __post_init__(self):
        integer(self.sort_seq)
        integer(self.local_id)


@dataclass(frozen=True)
class SourceRecord:
    account_id: str
    db_epoch: str
    conversation: str
    kind: str
    shard: str
    local_id: int
    sort_seq: int
    sender_id: str
    occurred_at: float
    received_at: float
    text: str = field(repr=False)
    message_type: str = "text"
    mentioned_ids: tuple[str, ...] = ()
    reply_to: str | None = None
    structure_evidence_ref: str | None = None

    def __post_init__(self):
        for value in (self.account_id, self.db_epoch, self.conversation, self.shard, self.sender_id, self.message_type):
            identifier(value)
        if self.kind not in {"dm", "group"}:
            raise SocialError("invalid_source_kind")
        integer(self.local_id, minimum=1)
        integer(self.sort_seq)
        timestamp(self.occurred_at)
        timestamp(self.received_at)
        text_value(self.text, 4000, empty=self.message_type != "text")
        if type(self.mentioned_ids) is not tuple or len(self.mentioned_ids) > 100:
            raise SocialError("invalid_mentions")
        for value in self.mentioned_ids:
            identifier(value)
        for value in (self.reply_to, self.structure_evidence_ref):
            if value is not None:
                identifier(value)
        if (self.mentioned_ids or self.reply_to is not None) and self.structure_evidence_ref is None:
            raise SocialError("unverified_message_structure")

    @property
    def position(self):
        return Cursor(self.sort_seq, self.local_id)

    @property
    def message_id(self):
        # Process/window restarts must not turn old records into new messages.
        return digest([self.account_id, self.db_epoch, self.kind, self.conversation, self.shard, self.local_id])

    @property
    def fingerprint(self):
        value = asdict(self)
        del value["received_at"]
        return digest(value)

    def verify_binding(self, binding, shard):
        if (self.account_id, self.db_epoch, self.conversation, self.kind, self.shard) != (
                binding.account.account_id, binding.account.db_epoch, binding.username, binding.kind, shard):
            raise SocialError("source_binding_mismatch")

    def event(self, binding):
        self.verify_binding(binding, self.shard)
        if self.message_type not in {"text", "image", "sticker", "voice"}:
            raise SocialError("unsupported_message_type")
        return IncomingEvent(binding.scope, self.sender_id, self.message_id, self.occurred_at,
                             self.received_at, self.text, source_verified=True,
                             is_self=self.sender_id == binding.account.self_id,
                             mention_self=binding.account.self_id in self.mentioned_ids, reply_to=self.reply_to)


def require_send_target(binding: ConversationBinding, action: OutgoingAction, *,
                        observed_account: str, observed_username: str, observed_window: WindowBinding):
    """Must run before touching input; names/aliases never authorize a target."""
    if (binding.kind != "dm" or binding.username != FILE_HELPER or action.scope != binding.scope
            or observed_account != binding.account.account_id or observed_username != FILE_HELPER
            or observed_window != binding.account.window):
        raise SocialError("wechat_send_target_denied")
    identifier(action.action_id)
    identifier(action.reply_to)
    text_value(action.text, 2000)


@dataclass(frozen=True)
class DeliveryEvidence:
    evidence_id: str
    action_id: str
    scope: ConversationScope
    binding_generation: str
    kind: str  # local_echo is not a delivery confirmation.
    reference: str
    content_sha256: str
    platform_message_id: str | None = None

    def __post_init__(self):
        for value in (self.evidence_id, self.action_id, self.binding_generation, self.reference):
            identifier(value)
        if not isinstance(self.scope, ConversationScope) or self.scope.platform != "wechat":
            raise SocialError("invalid_evidence_scope")
        if self.kind not in {"local_echo", "manual_confirmation"}:
            raise SocialError("unsupported_evidence_kind")
        if not isinstance(self.content_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise SocialError("invalid_content_fingerprint")
        if self.platform_message_id is not None:
            identifier(self.platform_message_id)
        if self.kind == "local_echo" and self.platform_message_id is None:
            raise SocialError("missing_echo_identity")
