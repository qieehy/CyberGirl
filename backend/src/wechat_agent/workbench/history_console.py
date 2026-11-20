"""Group-history-only HTTP surface, usable without a running WeChat process."""

from ..console import ConsoleServer, _fields
from ..social.models import SocialError
from ..web.assets import static_routes


class HistoryConsoleServer(ConsoleServer):
    def __init__(self, history, **kwargs):
        super().__init__(None, None, **kwargs)
        self.history = history
        self.static = static_routes("history")
        from .conversation_reads import ConversationReads
        self.reads = ConversationReads(history)

    async def start(self, port=8766):
        url = await super().start(port)
        self.reads.start()
        return url

    async def aclose(self):
        try:
            await super().aclose()
        finally:
            await self.reads.aclose()

    async def _route(self, method, path, data):
        if path.startswith("/api/wechat/reads"):
            from .conversation_reads import subscription_route
            return await subscription_route(self.reads, method, path, data)
        if method == "POST" and path == "/api/wechat/group-history":
            _fields(data, {"group", "limit", "refresh"})
            return await self.history.read(**data)
        raise SocialError("not_found")


async def run_history_console(database, port):
    from .group_history import history_for_workspace
    from .native import native_source_root

    server = HistoryConsoleServer(history_for_workspace(native_source_root(), database))
    try:
        print("群消息工作台；已保存记录可离线查看，更新时只读微信本地库：", flush=True)
        print(await server.start(port), flush=True)
        await server.server.serve_forever()
    finally:
        await server.aclose()
