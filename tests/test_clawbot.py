"""Protocol-only private chat; no native WeChat or Windows UI fixtures."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from wechat_agent.bridges.clawbot.client import ORIGIN, ClawBotClient, ClawBotError, approved_origin
from wechat_agent.bridges.clawbot.service import ClawBotService
from wechat_agent.bridges.clawbot.store import ingest
from wechat_agent.bridges.clawbot.vault import SessionVault
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError


class Protection:
    def protect(self, value):
        return bytes(b ^ 85 for b in value)

    unprotect = protect


class Client:
    token = None
    origin = ORIGIN

    def __init__(self, *, uncertain=False):
        self.sent, self.uncertain = [], uncertain

    async def login_qr(self):
        return dict(qrcode="qr-secret", qrcode_img_content="https://example.invalid/fixture-qr")

    async def login_status(self, *_args, **_kwargs):
        return dict(
            status="confirmed",
            bot_token="token-secret",
            ilink_bot_id="bot",
            ilink_user_id="owner",
            baseurl=ORIGIN,
        )

    async def updates(self, _):
        await asyncio.Event().wait()

    async def send(self, owner, text, context, client_id):
        self.sent.append((owner, text, context, client_id))
        if self.uncertain:
            raise ClawBotError("clawbot_network_error", uncertain=True)
        return dict(ret=0, message_id="server-fixture")

    async def aclose(self):
        pass


class Model:
    model = "fixture-model"

    def __init__(self):
        self.calls, self.entered, self.release = [], asyncio.Event(), None

    async def generate(self, text, history, *_):
        self.calls.append((text, history))
        self.entered.set()
        if self.release:
            await self.release.wait()
        return Decision(ActionKind.REPLY, "接口回复"), dict(
            inference_calls=1, model_http_attempts=1
        )

    async def catalog(self):
        return SimpleNamespace(models=[SimpleNamespace(slug="fixture-model")])


def setup(ledger, tmp_path, *, uncertain=False):
    client, model = Client(uncertain=uncertain), Model()
    s = ClawBotService(
        ledger, client, SessionVault(tmp_path / "session.bin", protection=Protection()), model
    )
    s._bind("bot", "owner")
    s.logged_in = s.reading = True
    client.token = "token-secret"
    return s, client, model


def message(key="1", *, sender="owner", text="你好", kind=1):
    return dict(
        message_id=key,
        from_user_id=sender,
        to_user_id="bot",
        message_type=kind,
        create_time_ms=int(time.time() * 1000),
        context_token="context-secret",
        item_list=[dict(type=1, text_item=dict(text=text))],
    )


def source(s, value):
    ingest(s.ledger, s.vault, s.binding, dict(msgs=[value], get_updates_buf="cursor-secret"))
    return s.ledger._db.execute(
        "SELECT rowid AS position,* FROM oc_messages ORDER BY rowid DESC LIMIT 1"
    ).fetchone()


def test_official_qr_and_single_uncertain_send_attempt():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("get_bot_qrcode"):
                assert request.method == "POST" and "Authorization" not in request.headers
                assert json.loads(request.content) == dict(local_token_list=[])
                return httpx.Response(
                    200, json=dict(qrcode="fixture", qrcode_img_content="fixture")
                )
            assert request.headers["Authorization"] == "Bearer fixture-token"
            assert json.loads(request.content)["msg"]["context_token"] == "context"
            raise httpx.ReadTimeout("synthetic", request=request)

        c = ClawBotClient(transport=httpx.MockTransport(handler), token="fixture-token")
        try:
            await c.login_qr()
            with pytest.raises(ClawBotError) as error:
                await c.send("owner", "reply", "context", "fixed-client-id")
            assert error.value.uncertain and len(requests) == 2
            with pytest.raises(ClawBotError):
                approved_origin("https://attacker.example")
        finally:
            await c.aclose()

    asyncio.run(run())


def test_owner_dedup_and_atomic_cursor(tmp_path):
    with Ledger(tmp_path / "receive.db") as ledger:
        s, _, _ = setup(ledger, tmp_path)
        own = message()
        ingest(
            ledger,
            s.vault,
            s.binding,
            dict(
                msgs=[own, message("other", sender="other"), message("self", sender="bot", kind=2)],
                get_updates_buf="cursor-secret",
            ),
        )
        ingest(ledger, s.vault, s.binding, dict(msgs=[own], get_updates_buf="cursor-secret"))
        assert ledger._db.execute("SELECT COUNT(*) FROM oc_messages").fetchone()[0] == 1
        assert (
            b"context-secret"
            not in ledger._db.execute("SELECT context FROM oc_messages").fetchone()[0]
        )
        with pytest.raises(SocialError, match="message_conflict"):
            ingest(
                ledger,
                s.vault,
                s.binding,
                dict(
                    msgs=[{**own, "item_list": [dict(type=1, text_item=dict(text="changed"))]}],
                    get_updates_buf="must-not-commit",
                ),
            )
        assert (
            s.vault.open(
                ledger._db.execute(
                    "SELECT value FROM oc_cursor WHERE scope=?", (s.binding.scope.key(),)
                ).fetchone()[0]
            )
            == "cursor-secret"
        )


@pytest.mark.parametrize("uncertain", [False, True])
def test_once_and_restart_no_replay(tmp_path, uncertain):
    async def run():
        path = tmp_path / "once.db"
        with Ledger(path) as ledger:
            s, c, m = setup(ledger, tmp_path, uncertain=uncertain)
            with pytest.raises(SocialError, match="consent_required"):
                s.auto_reply.set_enabled(True)
            s.auto_reply.set_enabled(True, consent=True)
            row = source(s, message())
            await s.reply(row)
            await s.reply(row)
            assert len(c.sent) == len(m.calls) == 1
            assert ledger._db.execute("SELECT state FROM oc_runs").fetchone()[0] == (
                "UNKNOWN" if uncertain else "API_ACCEPTED"
            )
            assert c.sent[0][:3] == ("owner", "接口回复", "context-secret")
            assert s.auto_reply.enabled is not uncertain
            await s.aclose()
        with Ledger(path) as ledger:
            s, c, m = setup(ledger, tmp_path)
            s.auto_reply.set_enabled(True)
            row = ledger._db.execute(
                "SELECT rowid AS position,* FROM oc_messages LIMIT 1"
            ).fetchone()
            await s.reply(row)
            assert c.sent == m.calls == []
            await s.aclose()

    asyncio.run(run())


def test_pause_cancels_pending_send(tmp_path):
    async def run():
        with Ledger(tmp_path / "pause.db") as ledger:
            s, c, m = setup(ledger, tmp_path)
            s.auto_reply.set_enabled(True, consent=True)
            m.release = asyncio.Event()
            task = asyncio.create_task(s.reply(source(s, message())))
            await m.entered.wait()
            s.auto_reply.set_enabled(False)
            m.release.set()
            await task
            assert (
                c.sent == []
                and ledger._db.execute("SELECT state FROM oc_runs").fetchone()[0] == "CANCELLED"
            )
            await s.aclose()

    asyncio.run(run())


def test_scan_binds_owner_but_does_not_enable_model(tmp_path):
    async def run():
        with Ledger(tmp_path / "login.db") as ledger:
            vault = SessionVault(tmp_path / "session.bin", protection=Protection())
            m, c = Model(), Client()
            s = ClawBotService(ledger, c, vault, m)
            qr = await s.start_login()
            assert qr["qr_png"].startswith("data:image/png;base64,")
            await s.login_task
            assert (
                s.logged_in
                and s.binding.username == "owner"
                and not s.auto_reply.enabled
                and not s.authorized()
            )
            assert m.calls == c.sent == [] and "token-secret" not in json.dumps(s.login_snapshot())
            assert vault.load()["bot_id"] == "bot"
            await s.aclose()

    asyncio.run(run())
