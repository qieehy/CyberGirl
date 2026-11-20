"""Model switching changes future generations without replaying old messages."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.test_private_auto_reply import build
from tests.test_workbench_private_local import incoming
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.models import selected_model


class Generator:
    model = "fixture"

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def catalog(self):
        return SimpleNamespace(models=[SimpleNamespace(slug=s) for s in ("fixture", "new-model")])

    async def __call__(self, *_):
        self.entered.set()
        await self.release.wait()
        return Decision(ActionKind.REPLY, "old model reply")


def test_switch_cancels_pending_generation_persists_and_does_not_send(tmp_path):
    async def run():
        with Ledger(tmp_path / "models.db") as ledger:
            generator = Generator()
            wb, sender = build(ledger, generator)
            wb.auto_reply.set_enabled(True)
            source = incoming(ledger)
            wb.auto_reply.tick()
            await generator.entered.wait()
            server = WorkbenchConsoleServer(ledger, wb)
            available = await server._route("GET", "/api/wechat/models", None)
            assert available == dict(selected="fixture", models=["fixture", "new-model"])
            result = await server._route(
                "POST", "/api/wechat/model", dict(model="new-model", expected_model="fixture")
            )
            assert result["changed"] and wb.model == generator.model == "new-model"
            assert wb.auto_reply.enabled and sender.calls == []
            assert selected_model(ledger, wb.binding.scope.key(), "fixture") == "new-model"
            assert (
                ledger._db.execute(
                    "SELECT COUNT(*) FROM workbench_drafts WHERE model_calls=1"
                ).fetchone()[0]
                == 1
            )
            assert (
                ledger._db.execute(
                    "SELECT state FROM workbench_drafts WHERE source_message_id=?",
                    (source.message_id,),
                ).fetchone()[0]
                != "READY"
            )
            assert not wb.model_switching
            wb.auto_reply.tick()
            assert sender.calls == []  # no second generation of the old source
            await wb.aclose()

    asyncio.run(run())


def test_invalid_or_stale_choice_preserves_current_model(tmp_path):
    async def run():
        with Ledger(tmp_path / "invalid.db") as ledger:
            generator = Generator()
            wb, sender = build(ledger, generator)
            wb.auto_reply.set_enabled(True)
            for model, expected, code in [
                ("not-in-catalog", "fixture", "model_unavailable"),
                ("new-model", "stale-model", "version_conflict"),
            ]:
                with pytest.raises(SocialError, match=code):
                    await wb.switch_model(model, expected)
            assert wb.model == "fixture" and wb.auto_reply.enabled and sender.calls == []
            assert (await wb.switch_model("fixture", "fixture"))["changed"] is False
            await wb.aclose()

    asyncio.run(run())
