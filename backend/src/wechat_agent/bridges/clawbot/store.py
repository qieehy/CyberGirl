"""Atomic cursor/inbox persistence and non-replayed protocol reply attempts."""

import math
from types import SimpleNamespace

from ...context import ConversationScope
from ...social.models import SocialError
from ...workbench.preferences import Preferences
from ..wechat.contracts import digest


def initialize(ledger):
    with ledger._transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS oc_messages(
            message_id TEXT PRIMARY KEY,scope TEXT NOT NULL REFERENCES scopes(scope),
            sender_id TEXT NOT NULL,text TEXT NOT NULL,kind TEXT NOT NULL,
            occurred_at REAL NOT NULL,received_at REAL NOT NULL,context BLOB NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS oc_cursor(
            scope TEXT PRIMARY KEY REFERENCES scopes(scope),value BLOB NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS oc_runs(
            draft_id TEXT PRIMARY KEY,source TEXT UNIQUE NOT NULL REFERENCES oc_messages(message_id),
            scope TEXT NOT NULL,model TEXT NOT NULL,model_calls INTEGER NOT NULL,
            state TEXT NOT NULL,text TEXT NOT NULL,reason TEXT,client_id TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL,trace TEXT NOT NULL DEFAULT '{}',receipt TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS oc_authorization(
            scope TEXT PRIMARY KEY REFERENCES scopes(scope),granted_at REAL NOT NULL,
            data_scope TEXT NOT NULL)""")
        db.execute(
            "UPDATE oc_runs SET state='UNKNOWN',reason='interrupted_operation' WHERE state IN ('GENERATING','SENDING')"
        )


def binding_for(ledger, bot_id, owner_id):
    scope = ConversationScope("weixin-oc", bot_id, "dm", owner_id)
    ledger.configure_owner(scope, owner_id)
    return SimpleNamespace(
        scope=scope,
        username=owner_id,
        generation=digest(["weixin-oc", bot_id, owner_id]),
        account=SimpleNamespace(account_id=bot_id, self_id=bot_id),
    )


def parse_message(msg, *, bot_id, owner_id, now):
    if (
        type(msg) is not dict
        or msg.get("from_user_id") != owner_id
        or msg.get("message_type") != 1
        or msg.get("to_user_id") not in (None, "", bot_id)
        or msg.get("group_id")
        or msg.get("chatroom_id")
        or "@chatroom" in owner_id
    ):
        return None
    platform_id = msg.get("message_id") or msg.get("msg_id")
    if type(platform_id) not in (str, int) or not 1 <= len(str(platform_id)) <= 128:
        return None
    at = msg.get("create_time_ms", msg.get("create_time"))
    if type(at) not in (int, float) or not math.isfinite(at) or at <= 0:
        return None
    at = at / 1000 if at > 1_000_000_000_000 else at
    if at > now + 60:
        return None
    context = msg.get("context_token")
    if not isinstance(context, str) or not 1 <= len(context) <= 16384:
        return None
    items = msg.get("item_list")
    if type(items) is not list or not 1 <= len(items) <= 32:
        return None
    parts, kind = [], "text"
    for item in items:
        if type(item) is not dict:
            return None
        if item.get("type") == 1:
            text = item.get("text_item", {}).get("text")
        elif item.get("type") == 3:
            text = item.get("voice_item", {}).get("text")
            kind = "voice"
        else:
            return dict(
                message_id=digest([bot_id, owner_id, str(platform_id)]),
                text="[暂未接入的媒体消息]",
                kind="unsupported",
                occurred_at=at,
                context=context,
            )
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            return None
        parts.append(text)
    text = "\n".join(parts)
    if not 1 <= len(text) <= 4000:
        return None
    return dict(
        message_id=digest([bot_id, owner_id, str(platform_id)]),
        text=text,
        kind=kind,
        occurred_at=at,
        context=context,
    )


def ingest(ledger, vault, binding, response):
    messages, cursor = response.get("msgs", []), response.get("get_updates_buf")
    if type(messages) is not list or len(messages) > 100:
        raise SocialError("clawbot_response_invalid")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 65536):
        raise SocialError("clawbot_response_invalid")
    now, fresh = ledger.now(), 0
    selected = [
        value
        for msg in messages
        if (
            value := parse_message(
                msg, bot_id=binding.account.account_id, owner_id=binding.username, now=now
            )
        )
        is not None
    ]
    with ledger._transaction() as db:
        for value in selected:
            old = db.execute(
                "SELECT text,kind,occurred_at FROM oc_messages WHERE message_id=?",
                (value["message_id"],),
            ).fetchone()
            if old:
                if (old["text"], old["kind"], old["occurred_at"]) != (
                    value["text"],
                    value["kind"],
                    value["occurred_at"],
                ):
                    raise SocialError("clawbot_message_conflict")
                continue
            if db.execute("SELECT COUNT(*) FROM oc_messages").fetchone()[0] >= 20000:
                raise SocialError("clawbot_storage_limit")
            db.execute(
                "INSERT INTO oc_messages VALUES(?,?,?,?,?,?,?,?)",
                (
                    value["message_id"],
                    binding.scope.key(),
                    binding.username,
                    value["text"],
                    value["kind"],
                    value["occurred_at"],
                    now,
                    vault.seal(value["context"]),
                ),
            )
            fresh += 1
        if cursor:
            db.execute(
                "INSERT INTO oc_cursor VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET value=excluded.value",
                (binding.scope.key(), vault.seal(cursor)),
            )
    return fresh


class ClawBotPreferences(Preferences):
    def logs(self):
        db = self.ledger._db
        events = []
        for row in db.execute(
            "SELECT * FROM oc_runs WHERE scope=? ORDER BY created_at DESC LIMIT 100", (self.scope,)
        ):
            events.append(
                dict(
                    at=row["created_at"],
                    kind="规则" if not row["model_calls"] else "模型",
                    status=row["state"],
                    detail=row["reason"] or row["model"],
                )
            )
        now = self.ledger.now()
        midnight = now - (now + 8 * 3600) % 86400
        return dict(
            events=events,
            stats=dict(
                messages=db.execute(
                    "SELECT COUNT(*) FROM oc_messages WHERE scope=?", (self.scope,)
                ).fetchone()[0],
                model_calls_today=db.execute(
                    "SELECT COUNT(*) FROM oc_runs WHERE scope=? AND created_at>=? AND model_calls=1",
                    (self.scope, midnight),
                ).fetchone()[0],
                sent_with_echo=0,
                api_accepted=db.execute(
                    "SELECT COUNT(*) FROM oc_runs WHERE scope=? AND state='API_ACCEPTED'",
                    (self.scope,),
                ).fetchone()[0],
            ),
        )
