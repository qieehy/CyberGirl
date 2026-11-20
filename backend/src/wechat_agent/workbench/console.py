"""Authenticated real-mode routes, isolated from synthetic dispatch endpoints."""

from ..console import ConsoleServer, _fields
from ..social.models import SocialError
from ..web.assets import static_routes


class WorkbenchConsoleServer(ConsoleServer):
    def __init__(self, ledger, workbench, **kwargs):
        super().__init__(ledger, None, **kwargs)
        self.workbench = workbench
        self.static = static_routes("workbench")
        self.group_history = None
        self.reads = None

    def _history(self):
        if self.group_history is None:
            from .group_history import history_for_workspace
            from .native import native_source_root
            self.group_history = history_for_workspace(native_source_root(), self.ledger.path)
        return self.group_history

    async def start(self, port=8765):
        # Restore subscriptions only for an already registered native account.
        if self.ledger._db.execute("SELECT 1 FROM sqlite_master WHERE name='reader_accounts'").fetchone():
            if self.ledger._db.execute("SELECT COUNT(*) FROM reader_accounts").fetchone()[0] == 1:
                from .conversation_reads import ConversationReads
                self.reads = ConversationReads(self._history())
        url = await super().start(port)
        if self.reads:
            self.reads.start()
        return url

    async def aclose(self):
        try:
            await super().aclose()
        finally:
            if self.reads:
                await self.reads.aclose()

    async def _route(self, method, path, data):
        wb = self.workbench
        if path.startswith("/api/wechat/reads"):
            from .conversation_reads import ConversationReads, subscription_route
            if self.reads is None:
                self.reads = ConversationReads(self._history())
                self.reads.start()
            return await subscription_route(self.reads, method, path, data)
        if path.startswith('/api/wechat/companion'):
            companion = getattr(wb, 'companion', None)
            if companion is None:
                raise SocialError('companion_unavailable')
            if method == 'GET' and path == '/api/wechat/companion':
                return companion.snapshot()
            if method != 'POST':
                raise SocialError('not_found')
            if path == '/api/wechat/companion/pause':
                _fields(data, set())
                return companion.stop()
            async with wb.operation_lock:
                if path == '/api/wechat/companion/config':
                    _fields(data, {'expected_revision','config','profile','role'})
                    return companion.save(**data)
                if path == '/api/wechat/companion/memory':
                    if type(data) is not dict or set(data) not in (
                            {'content','kind','importance','pinned'},
                            {'id','expected_version','content','kind','importance','pinned'}):
                        raise SocialError('invalid_request')
                    return companion.put_memory(**data)
                if path == '/api/wechat/companion/forget':
                    _fields(data, {'id','expected_version','restore'})
                    return companion.forget(**data)
                if path == '/api/wechat/companion/clear':
                    _fields(data, {'expected_revision'})
                    return companion.clear(**data)
                if path == '/api/wechat/companion/preview':
                    _fields(data, {'query'})
                    if not isinstance(data['query'], str) or len(data['query'])>2000:
                        raise SocialError('invalid_request')
                    return dict(context=companion.context(data['query']), model_calls=0, sends=0)
                if path == '/api/wechat/companion/summary':
                    _fields(data, set())
                    return companion.summarize_once()
            raise SocialError('not_found')
        if method == 'GET' and path == '/api/wechat/preferences':
            return wb.preferences.snapshot()
        if method == 'GET' and path == '/api/wechat/logs':
            return wb.preferences.logs()
        if method == 'GET' and path == '/api/wechat/personas':
            return dict(versions=[dict(r) for r in self.ledger._db.execute('SELECT version,name,style,notes,created_at FROM personas ORDER BY version DESC LIMIT 30')])
        if method == 'POST' and path in {'/api/wechat/config','/api/wechat/rules'}:
            key = 'config' if path.endswith('config') else 'rules'
            _fields(data, {'expected_revision', key})
            wb._open()
            async with wb.operation_lock:
                return wb.preferences.save(**data)
        if method == 'POST' and path == '/api/wechat/rules/preview':
            from .preferences import match_rule
            _fields(data, {'text'})
            if not isinstance(data['text'], str) or len(data['text']) > 4000:
                raise SocialError('invalid_request')
            rule = match_rule(wb.preferences.snapshot()['rules'], data['text'])
            return dict(matched=rule, preview_only=True)
        if method == 'POST' and path == '/api/wechat/auto-reply':
            _fields(data, {'enabled'})
            return wb.auto_reply.set_enabled(**data)
        if method == "POST" and path == "/api/wechat/group-history":
            _fields(data, {"group", "limit", "refresh"})
            if self.group_history is None:
                from .group_history import history_for_workspace
                from .native import native_source_root
                self.group_history = history_for_workspace(native_source_root(), self.ledger.path)
            return await self.group_history.read(**data)
        if method == "GET" and path == "/api/wechat/state":
            return wb.snapshot()
        if method == "GET" and path == "/api/wechat/models":
            return await wb.models()
        if method == "POST" and path == "/api/wechat/model":
            _fields(data, {"model", "expected_model"})
            return await wb.switch_model(**data)
        if method == "GET" and path == "/api/persona":
            return self.ledger.current_persona().to_dict()
        prefix = "/api/wechat/drafts/"
        if method == "GET" and path.startswith(prefix):
            return wb.state.draft(path[len(prefix):])
        if method != "POST":
            raise SocialError("not_found")
        if path == "/api/persona":
            _fields(data, {"expected_version", "name", "style", "notes"})
            return self.ledger.save_persona(**data).to_dict()
        if path == "/api/wechat/drafts":
            _fields(data, {"request_id", "source_message_id", "expected_message_revision", "expected_persona_version"})
            return await wb.generate(**data)
        if path == "/api/wechat/local-drafts":
            _fields(data, {"request_id", "source_message_id", "expected_message_revision", "expected_persona_version", "text"})
            return await wb.compose(**data)
        if path == "/api/wechat/local-drafts/preview":
            _fields(data, {"draft_id", "expected_version"})
            return wb.preview(**data)
        if path.startswith(prefix):
            tail = path[len(prefix):].split("/")
            if len(tail) == 2 and tail[1] == "edit":
                _fields(data, {"request_id", "expected_version", "text"})
                return wb.state.edit(tail[0], **data)
            if len(tail) == 2 and tail[1] == "cancel":
                _fields(data, set())
                return wb.state.cancel(tail[0])
        if path == "/api/wechat/send/prepare":
            _fields(data, {"request_id", "draft_id", "expected_version"})
            return await wb.prepare(**data)
        if path == "/api/wechat/private-send":
            _fields(data, {"request_id", "text"})
            return await wb.send_private(**data)
        if path == "/api/wechat/private-send/reconcile":
            _fields(data, {"action_id"})
            if wb.private_sender is None:
                raise SocialError("wechat_send_disabled")
            return wb.private_sender.reconcile_existing(**data)
        if path == "/api/wechat/send/confirm":
            _fields(data, {"request_id", "confirmation_id"})
            return await wb.confirm(**data)
        action_prefix = "/api/wechat/actions/"
        if path.startswith(action_prefix) and path.endswith("/phone-confirmation"):
            _fields(data, {"confirmed_complete_text"})
            if data["confirmed_complete_text"] is not True:
                raise SocialError("phone_confirmation_required")
            return wb.phone_confirmation(path[len(action_prefix):-len("/phone-confirmation")])
        if path == "/api/wechat/pause":
            _fields(data, {"expected_version", "paused"})
            result = await wb.pause(**data)
            if self.reads:
                if data["paused"]:
                    await self.reads.aclose()
                else:
                    self.reads.start()
            return result
        if path == "/api/wechat/disconnect":
            _fields(data, set())
            if self.reads:
                await self.reads.aclose()
            await wb.aclose()
            return dict(connected=False)
        raise SocialError("not_found")


async def run_workbench_console(database, port, *, live_model, state_directory, native_options):
    from ..social.ledger import Ledger
    from .native import open_native_receiver
    from .service import LiveDraftGenerator, WeChatWorkbenchService

    with Ledger(database) as ledger:
        if ledger._db.execute("SELECT paused FROM control").fetchone()[0]:
            # Starting explicitly is the operator's resume action. No pending
            # model or delivery work is replayed by this transition.
            ledger.pause(False)
        receiver, sender = await open_native_receiver(ledger, **native_options)
        wb = WeChatWorkbenchService(ledger, receiver, sender=sender, model=live_model,
            generator=LiveDraftGenerator(ledger, live_model, state_directory) if live_model else None)
        server = WorkbenchConsoleServer(ledger, wb)
        try:
            url = await server.start(port)
            wb.start()
            print("文件传输助手工作台；仅点击后生成，逐条核对后发送：", flush=True)
            print(url, flush=True)
            await server.server.serve_forever()
        finally:
            try:
                await server.aclose()
            finally:
                await wb.aclose()


async def run_private_workbench_console(database, port, *, local_drafts=False, native_options, live_model=None, state_directory=None, send=False, auto_reply=False, media=False):
    """Selected target; optional explicit model drafts, never a sender."""
    from ..social.ledger import Ledger
    from .private_native import open_private_receiver
    from .service import LiveDraftGenerator, WeChatWorkbenchService

    with Ledger(database) as ledger:
        if ledger._db.execute("SELECT paused FROM control").fetchone()[0]:
            ledger.pause(False)
        receiver = await open_private_receiver(ledger, media=media, **native_options)
        wb = server = None
        try:
            from .private_sender import PrivateBackgroundSender
            private_sender = PrivateBackgroundSender(ledger, receiver, native_options["scope_file"]) if send else None
            from .media import PrivateMediaResolver
            resolver = PrivateMediaResolver(ledger, receiver) if media else None
            if live_model:
                from .models import selected_model
                live_model = selected_model(ledger, receiver.binding.scope.key(), live_model)
            wb = WeChatWorkbenchService(ledger, receiver, local_drafts=local_drafts or bool(live_model),
                private_model=bool(live_model), model=live_model,
                private_sender=private_sender,
                generator=LiveDraftGenerator(ledger, live_model, state_directory, media_resolver=resolver) if live_model else None)
            server = WorkbenchConsoleServer(ledger, wb)
            url = await server.start(port)
            wb.start()
            if auto_reply:
                wb.auto_reply.set_enabled(True)
            print("指定私聊工作台；" + ("自动回复" if auto_reply else "手动草稿") + ("；后台发送已启用：" if send else "；微信发送关闭："), flush=True)
            print(url, flush=True)
            await server.server.serve_forever()
        finally:
            try:
                if server:
                    await server.aclose()
            finally:
                if wb:
                    await wb.aclose()
                else:
                    await receiver.close()
