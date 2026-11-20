"""Loopback-only sandbox console with explicit browser authentication."""

import asyncio
import json
from pathlib import Path

from .context import ConversationScope
from .social.decision import RuntimeDecider, SyntheticModel
from .social.groups import GroupBuffer, configure_group
from .social.ledger import Ledger
from .social.models import IncomingEvent, SocialError
from .web.assets import static_routes
from .web.server import HttpServer
from .web.validation import fields as _fields
from .web.validation import parse_json as _json  # noqa: F401 - compatibility export
from .social.sandbox import FakeAdapter
from .social.service import SocialService

SCOPE = ConversationScope("fake", "sandbox-account", "dm", "owner-dm")
GROUP_SCOPE = ConversationScope("fake", "sandbox-account", "group", "practice-group")


class ConsoleServer(HttpServer):
    def __init__(self, ledger, service, *, token=None, preview=None, groups=None):
        super().__init__(token=token, static=static_routes("sandbox"))
        self.ledger, self.service = ledger, service
        self.preview, self.groups = preview, groups

    async def _route(self, method, path, data):
        if method == "GET" and path == "/api/state":
            result = self.ledger.console_snapshot(SCOPE)
            result["live_preview"] = {"enabled": self.preview is not None,
                                      "model": self.preview.model if self.preview else None,
                                      "runs": self.preview.recent() if self.preview else []}
            result["group_sandbox"] = {"enabled": self.groups is not None,
                                       "events": self.ledger.console_snapshot(GROUP_SCOPE)["events"] if self.groups else []}
            return result
        if method != "POST":
            raise SocialError("not_found")
        if path == "/api/group/messages":
            _fields(data, {"message_id", "occurred_at", "text", "sender_id", "trigger"})
            if self.groups is None:
                raise SocialError("group_sandbox_disabled")
            if (not isinstance(data["sender_id"], str) or not isinstance(data["trigger"], str)
                    or data["sender_id"] not in {"member-a", "member-b"} or data["trigger"] not in {"none", "mention", "reply"}):
                raise SocialError("invalid_request")
            reply_to = None
            if data["trigger"] == "reply":
                rows = self.ledger.console_snapshot(GROUP_SCOPE)["events"]
                reply_to = next((r["receipt_id"] for r in rows if r["delivery_state"] == "CONFIRMED"), None)
                if reply_to is None:
                    raise SocialError("no_confirmed_group_reply")
            event = IncomingEvent(GROUP_SCOPE, data["sender_id"], data["message_id"], data["occurred_at"], self.ledger.now(),
                                  data["text"], source_verified=True, mention_self=data["trigger"] == "mention", reply_to=reply_to)
            return self.groups.submit(event)
        if path == "/api/group/dispatch":
            _fields(data, {"action_id"})
            if self.groups is None or not isinstance(data["action_id"], str) or not self.ledger.action_in_scope(GROUP_SCOPE, data["action_id"]):
                raise SocialError("unknown_action")
            return await self.service.dispatch(data["action_id"])
        if path == "/api/preview":
            _fields(data, {"request_id", "expected_version", "text"})
            if self.preview is None:
                raise SocialError("live_preview_disabled")
            return await self.preview.run(**data)
        if path == "/api/persona":
            _fields(data, {"expected_version", "name", "style", "notes"})
            return self.ledger.save_persona(**data).to_dict()
        if path == "/api/pause":
            _fields(data, {"expected_version", "paused"})
            state = self.ledger.console_snapshot(SCOPE)
            if type(data["expected_version"]) is not int or data["expected_version"] != state["policy_version"]:
                raise SocialError("version_conflict")
            self.ledger.pause(data["paused"])
            return {"paused": data["paused"]}
        if path == "/api/messages":
            _fields(data, {"message_id", "occurred_at", "text"})
            event = IncomingEvent(SCOPE, "sandbox-owner", data["message_id"], data["occurred_at"], self.ledger.now(),
                                  data["text"], source_verified=True)
            return await self.service.prepare(event)
        if path == "/api/dispatch":
            _fields(data, {"action_id"})
            if not isinstance(data["action_id"], str) or not self.ledger.action_in_scope(SCOPE, data["action_id"]):
                raise SocialError("unknown_action")
            return await self.service.dispatch(data["action_id"])
        if path == "/api/replay":
            _fields(data, {"event_id"})
            if not isinstance(data["event_id"], str):
                raise SocialError("invalid_request")
            event = self.ledger.recorded_event(SCOPE, data["event_id"])
            return await self.service.prepare(event)
        raise SocialError("not_found")


async def run_console(database: Path, port: int, *, live_model=None, state_directory=None):
    from .preview import PreviewService

    with Ledger(database) as ledger:
        ledger.configure_owner(SCOPE, "sandbox-owner")
        configure_group(ledger, GROUP_SCOPE, bot_id="sandbox-bot", consent_ref="synthetic-practice-consent", initialize=True)
        async with SocialService(ledger, RuntimeDecider(SyntheticModel()), [FakeAdapter()]) as service, GroupBuffer(service) as groups:
            preview = PreviewService(ledger, live_model, state_directory) if live_model else None
            server = ConsoleServer(ledger, service, preview=preview, groups=groups)
            try:
                url = await server.start(port)
                print("本地沙盒控制台已启动；在浏览器打开以下地址（含本次启动的访问凭证）：", flush=True)
                print(url, flush=True)
                print("主人练习仍为离线模拟；人格页真实预演仅在点击后调用订阅。" if preview else
                      "仅离线模拟，不调用真实模型或微信。", flush=True)
                print("按 Ctrl+C 关闭。", flush=True)
                await server.server.serve_forever()
            finally:
                await server.aclose()
