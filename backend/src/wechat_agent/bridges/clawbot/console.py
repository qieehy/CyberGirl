"""ClawBot login surface and existing studio management, no native WeChat imports."""

import asyncio
import json
from pathlib import Path

from ...console import _fields
from ...social.ledger import Ledger
from ...social.models import SocialError
from ...workbench.console import WorkbenchConsoleServer
from .client import ClawBotClient
from .model import ClawBotModel
from .service import ClawBotService
from .vault import SessionVault


class ClawBotConsoleServer(WorkbenchConsoleServer):
    def __init__(self, ledger, workbench, **kwargs):
        super().__init__(ledger, workbench, **kwargs)

    async def _route(self, method, path, data):
        wb = self.workbench
        if method == "GET" and path == "/api/clawbot/login":
            return wb.login_snapshot()
        if method == "POST" and path == "/api/clawbot/login":
            _fields(data, set())
            return await wb.start_login()
        if method == "POST" and path == "/api/clawbot/pin":
            _fields(data, {"pin"})
            return wb.submit_pin(data["pin"])
        if method == "POST" and path == "/api/wechat/auto-reply":
            if type(data) is not dict or set(data) not in ({"enabled"}, {"enabled", "consent"}):
                raise SocialError("invalid_request")
            return wb.auto_reply.set_enabled(**data)
        if (
            path.startswith("/api/wechat/drafts")
            or path.startswith("/api/wechat/private-send")
            or path
            in {
                "/api/wechat/group-history",
                "/api/wechat/local-drafts",
                "/api/wechat/local-drafts/preview",
                "/api/wechat/send/prepare",
                "/api/wechat/send/confirm",
            }
        ):
            raise SocialError("clawbot_private_auto_only")
        return await super()._route(method, path, data)


async def run_clawbot_console(
    database, port, *, directory, session_file, model, bootstrap_profile=None, resume=False
):
    bootstrap = (
        json.loads(await asyncio.to_thread(Path(bootstrap_profile).read_text, encoding="utf-8"))
        if bootstrap_profile
        else None
    )
    with Ledger(Path(database)) as ledger:
        client = ClawBotClient()
        wb = ClawBotService(
            ledger,
            client,
            SessionVault(session_file),
            ClawBotModel(model, directory),
            bootstrap=bootstrap,
            resume=resume,
        )
        server = ClawBotConsoleServer(ledger, wb)
        try:
            await wb.start()
            print("ClawBot 接口私聊；不依赖微信桌面窗口；等待扫码或登录恢复：", flush=True)
            print(await server.start(port), flush=True)
            await server.server.serve_forever()
        finally:
            await server.aclose()
            await wb.aclose()
