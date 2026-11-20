"""Local commands for the synthetic model demo and independent Codex authentication."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .demo import run_demo
from .providers.codex import ErrorCode, ProviderError
from .providers.codex.auth import CodexAuthClient
from .providers.codex.auth_models import AuthEndpoint, DeviceChallenge
from .providers.codex.credentials import CredentialStore
from .providers.codex.token_manager import TokenManager, storage_call


async def auth_command(action: str, directory: Path) -> dict[str, object]:
    endpoint = AuthEndpoint()
    store = CredentialStore(directory, endpoint.binding)
    if action == "logout":
        try:
            await storage_call(store.acquire)
            await storage_call(store.reset)
        finally:
            await storage_call(store.close)
        return {"state": "logged_out", "local_only": True}
    async with TokenManager(store, CodexAuthClient(endpoint)) as manager:
        if action == "login":
            async def show_challenge(challenge: DeviceChallenge) -> None:
                # This is the explicit login screen, never a log/status payload.
                print(f"请在浏览器打开：{challenge.verification_uri}", flush=True)
                print(f"输入一次性代码：{challenge.user_code}", flush=True)
                print("等待登录；按 Ctrl+C 取消。", flush=True)

            await manager.login(show_challenge)
        return manager.status()


async def models_command(directory: Path) -> dict:
    from .providers.codex.catalog import fetch_catalog

    endpoint = AuthEndpoint()
    async with TokenManager(CredentialStore(directory, endpoint.binding), CodexAuthClient(endpoint)) as manager:
        epoch = manager.context_key()
        catalog = await fetch_catalog(await manager.get_session())
        if catalog.context_key != epoch or manager.context_key() != epoch:
            from .providers.codex import ErrorCode

            raise ProviderError(ErrorCode.AUTH)
        return catalog.summary()


def chat_command(mode: str, model: str | None, state_directory: Path, once: str | None = None) -> None:
    # Console input stays on the main thread outside the event loop. No blocked
    # input() worker can keep asyncio shutdown alive after EOF or Ctrl+C.
    from .bootstrap import open_live_application, open_mock_application
    from .context import LOCAL_SCOPE

    context = (open_mock_application() if mode == "mock" else open_live_application(model, state_directory))
    with asyncio.Runner() as runner:
        app = runner.run(context.__aenter__())
        try:
            print("模式：本机模拟对话" if mode == "mock" else "模式：ChatGPT 订阅对话", flush=True)
            if once is None and sys.stdin.isatty():
                print("输入消息；/reset 清空当前上下文，/logout 本地退出，/exit 结束。", flush=True)

            async def show_answer(draft) -> bool:
                print(draft.text, flush=True)
                return True

            while True:
                if once is not None:
                    text = once
                else:
                    if sys.stdin.isatty():
                        print("你> ", end="", flush=True)
                    line = sys.stdin.readline()
                    if not line:
                        break
                    text = line.rstrip("\r\n")
                if text == "/exit":
                    break
                if text == "/reset":
                    runner.run(app.engine.reset(LOCAL_SCOPE))
                    print("当前上下文已清空。", flush=True)
                elif text == "/logout":
                    runner.run(app.logout())
                    print("已本地退出。", flush=True)
                    break
                elif text.strip():
                    try:
                        runner.run(app.engine.turn(LOCAL_SCOPE, text, show_answer))
                    except ProviderError as exc:
                        recoverable = {
                            ErrorCode.INPUT: "输入不符合要求，请缩短或调整消息。",
                            ErrorCode.LIMIT: "本轮内容超过限制，请缩短消息或清空上下文。",
                            ErrorCode.CONNECTION: "连接失败，请检查代理和网络。",
                            ErrorCode.TIMEOUT: "本轮请求超时。",
                            ErrorCode.RATE_LIMIT: "服务暂时限流，请稍后再试。",
                            ErrorCode.HTTP: "服务暂时返回错误。",
                            ErrorCode.INCOMPLETE: "响应未完整结束，本轮没有显示回答。",
                            ErrorCode.REFUSAL: "模型未提供本轮回答，请调整问题。",
                            ErrorCode.PROTOCOL: "响应格式不兼容，本轮没有显示回答。",
                        }
                        if once is not None or exc.code not in recoverable:
                            raise
                        print(recoverable[exc.code] + " 已保留此前上下文，未自动重发。", file=sys.stderr, flush=True)
                        if exc.retry_after is not None:
                            print(f"建议等待 {exc.retry_after:g} 秒。", file=sys.stderr, flush=True)
                if once is not None:
                    break
        finally:
            runner.run(context.__aexit__(None, None, None))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="wechat-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the H1 local HTTP demonstration")
    history = commands.add_parser("history", help="offline group history and read-only local database refresh")
    clawbot = commands.add_parser('clawbot', help='window-independent owner-only Weixin ClawBot private chat')
    clawbot.add_argument('--database', type=Path, default=Path.cwd()/'.state/clawbot/messages.sqlite3')
    clawbot.add_argument('--session-file', type=Path, default=Path.cwd()/'.state/clawbot/session.dpapi')
    clawbot.add_argument('--state-dir', type=Path, default=Path.cwd()/'.state/codex')
    clawbot.add_argument('--live-model', default='gpt-5.6-luna')
    clawbot.add_argument('--bootstrap-profile', type=Path)
    clawbot.add_argument('--auto-reply', action='store_true', help='resume only a previously authorized scanning owner')
    clawbot.add_argument('--port', type=int, default=8765)
    history.add_argument("--database", type=Path, default=Path.cwd()/".state/social/wechat-sender-poc.sqlite3")
    history.add_argument("--port", type=int, default=8766)
    social = commands.add_parser("social-demo", help="persistent synthetic inbox/decision/outbox demonstration")
    social.add_argument("--database", type=Path, default=Path.cwd() / ".state" / "social" / "sandbox.sqlite3")
    console = commands.add_parser("console", help="local persona editor and synthetic conversation replay UI")
    console.add_argument("--database", type=Path)
    console.add_argument("--mode", choices=("sandbox", "wechat", "wechat-private"), default="sandbox")
    console.add_argument("--wechat-account-evidence", type=Path)
    console.add_argument("--wechat-private-scope", type=Path)
    console.add_argument("--local-drafts", action="store_true", help="private mode: local operator drafts and previews only")
    console.add_argument("--auto-reply", action="store_true", help="new selected-peer inbound triggers model and background send")
    console.add_argument("--media", action="store_true", help="private chat: locally decode images/stickers, transcribe voice and include in replies")
    console.add_argument("--wechat-pid", type=int)
    console.add_argument("--wechat-hwnd", type=int)
    console.add_argument("--wechat-created-filetime", type=int)
    console.add_argument("--wechat-send", choices=("off", "confirm"), default="off")
    console.add_argument("--review-unknown-action", action="append", default=[])
    console.add_argument("--uncertainty-review-reference")
    console.add_argument("--port", type=int, default=8765)
    console.add_argument("--live-model", help="enable explicit persona previews with this account model slug")
    console.add_argument("--state-dir", type=Path, default=Path.cwd() / ".state" / "codex")
    auth = commands.add_parser("auth", help="independent Codex login, status, or local logout")
    auth.add_argument("action", choices=["login", "status", "logout"])
    auth.add_argument("--state-dir", type=Path, default=Path.cwd() / ".state" / "codex",
                      help="application-only credential directory; default: .state/codex")
    chat = commands.add_parser("chat", help="text conversation through the actual AI_agent Runtime")
    chat.add_argument("--mode", required=True, choices=["mock", "live"])
    chat.add_argument("--model", help="explicit account model slug required for live mode")
    chat.add_argument("--state-dir", type=Path, default=Path.cwd() / ".state" / "codex")
    chat.add_argument("--once", help="one text turn, then close")
    models = commands.add_parser("models", help="fetch this application's live account model catalog")
    models.add_argument("--state-dir", type=Path, default=Path.cwd() / ".state" / "codex")
    verify = commands.add_parser("verify", help="one explicit H4 live request; no automatic sequence/replay")
    verify.add_argument("--stage", choices=["provider", "runtime", "restart"], required=True)
    verify.add_argument("--model", required=True, help="model slug from the live account catalog")
    verify.add_argument("--state-dir", type=Path, default=Path.cwd() / ".state" / "codex")
    verify.add_argument("--output", type=Path, required=True, help="new redacted record; an existing file stops execution")
    args = parser.parse_args()
    if args.command == "chat" and args.mode == "live" and not args.model:
        parser.error("chat --mode live requires --model from the account catalog")
    try:
        if args.command == "history":
            from .social.models import SocialError
            from .workbench.history_console import run_history_console
            try:
                asyncio.run(run_history_console(args.database,args.port))
            except SocialError as exc:
                parser.exit(1,json.dumps({"error":exc.code})+"\n")
            return
        if args.command == "console":
            from .console import run_console
            from .social.models import SocialError

            try:
                if args.mode in {"wechat", "wechat-private"}:
                    from .workbench.console import (
                        run_private_workbench_console,
                        run_workbench_console,
                    )
                    from .workbench.native import native_source_root

                    if args.mode == "wechat-private":
                        if args.media and not args.live_model:
                            parser.error('--media requires a live model in private mode')
                        if args.auto_reply and (not args.live_model or args.wechat_send != 'confirm'):
                            parser.error('auto-reply requires a live model and --wechat-send confirm')
                        if (args.wechat_account_evidence
                                or args.review_unknown_action or args.uncertainty_review_reference):
                            parser.error("wechat-private mode does not permit send or filehelper review options")
                        evidence = args.wechat_private_scope
                    else:
                        if args.wechat_private_scope or args.local_drafts or args.auto_reply or args.media:
                            parser.error("private scope and local drafts require --mode wechat-private")
                        evidence = args.wechat_account_evidence
                    if not all((evidence, args.wechat_pid, args.wechat_hwnd, args.wechat_created_filetime)):
                        parser.error("WeChat modes require explicit scope/account evidence, PID, HWND and created-filetime")
                    original = native_source_root() / ".state" / "social" / "wechat-sender-poc.sqlite3"
                    if args.database and args.database.resolve() != original.resolve():
                        if args.mode != 'wechat-private':
                            parser.error("wechat mode must reuse the original sender ledger")
                        import sqlite3

                        from .bridges.wechat.private_receive import private_scope
                        selected=private_scope(json.loads(args.wechat_private_scope.read_text(encoding='utf-8')))
                        allowed=native_source_root()/'.state/social/accounts'/selected['account_fingerprint']/'wechat.sqlite3'
                        with sqlite3.connect(original.as_uri()+'?mode=ro',uri=True) as source_ledger:
                            same=source_ledger.execute('SELECT 1 FROM reader_accounts WHERE fingerprint=?',(selected['account_fingerprint'],)).fetchone()
                        if same or args.database.resolve()!=allowed.resolve():
                            parser.error('alternate ledger is allowed only for a distinct explicitly selected account')
                        original=allowed
                    if not original.is_file():
                        parser.error("original WeChat sender ledger is required; it is never created implicitly")
                    if args.mode == "wechat-private":
                        model_options = dict(live_model=args.live_model, state_directory=args.state_dir) if args.live_model else {}
                        if args.wechat_send == "confirm":
                            model_options["send"] = True
                        if args.auto_reply:
                            model_options['auto_reply'] = True
                        if args.media:
                            model_options['media'] = True
                        asyncio.run(run_private_workbench_console(original, args.port, local_drafts=args.local_drafts,
                            native_options=dict(scope_file=args.wechat_private_scope, pid=args.wechat_pid,
                                hwnd=args.wechat_hwnd, created_filetime=args.wechat_created_filetime), **model_options))
                    else:
                        asyncio.run(run_workbench_console(original, args.port, live_model=args.live_model,
                            state_directory=args.state_dir, native_options=dict(evidence_path=args.wechat_account_evidence,
                                pid=args.wechat_pid, hwnd=args.wechat_hwnd, created_filetime=args.wechat_created_filetime,
                                send=args.wechat_send == "confirm", reviewed_unknowns=args.review_unknown_action,
                                uncertainty_reference=args.uncertainty_review_reference)))
                else:
                    if (args.wechat_send != "off" or args.wechat_account_evidence or args.wechat_pid or args.wechat_hwnd
                            or args.wechat_created_filetime or args.review_unknown_action or args.uncertainty_review_reference
                            or args.wechat_private_scope or args.local_drafts or args.auto_reply):
                        parser.error("WeChat options require --mode wechat or wechat-private")
                    database = args.database or Path.cwd() / ".state" / "social" / "sandbox.sqlite3"
                    asyncio.run(run_console(database, args.port, live_model=args.live_model,
                                            state_directory=args.state_dir))
            except SocialError as exc:
                parser.exit(1, json.dumps({"error": exc.code}) + "\n")
            return
        if args.command == 'clawbot':
            from .bridges.clawbot.console import run_clawbot_console
            from .social.models import SocialError
            try:
                asyncio.run(run_clawbot_console(args.database, args.port, directory=args.state_dir,
                    session_file=args.session_file, model=args.live_model,
                    bootstrap_profile=args.bootstrap_profile, resume=args.auto_reply))
            except SocialError as exc:
                parser.exit(1, json.dumps({'error': exc.code})+'\n')
            return
        if args.command == "social-demo":
            from .social.models import SocialError
            from .social.sandbox import run_social_demo

            try:
                print(json.dumps(asyncio.run(run_social_demo(args.database)), ensure_ascii=False, indent=2))
            except SocialError as exc:
                parser.exit(1, json.dumps({"error": exc.code}) + "\n")
            return
        if args.command == "chat":
            chat_command(args.mode, args.model, args.state_dir, args.once)
            return
        if args.command == "verify":
            from .verification import verify_command

            result = asyncio.run(verify_command(args.stage, args.model, args.state_dir, args.output))
        elif args.command == "models":
            result = asyncio.run(models_command(args.state_dir))
        else:
            result = asyncio.run(run_demo() if args.command == "demo" else auth_command(args.action, args.state_dir))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "verify" and result["status"] != "passed":
            parser.exit(1)
    except ProviderError as exc:
        parser.exit(1, json.dumps({"error": exc.code.value, "http_status": exc.status,
                                   "request_id": exc.request_id, "retry_after": exc.retry_after}) + "\n")
    except KeyboardInterrupt:
        parser.exit(130, "操作已取消。\n")
    except OSError:
        parser.exit(1, "无法读写本机文件；请检查目录与权限。\n")


if __name__ == "__main__":
    main()
