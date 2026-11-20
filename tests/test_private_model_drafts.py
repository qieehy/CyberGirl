"""Critical manual-model boundaries only; synthetic generator, no real requests."""
import asyncio

import pytest

from tests.test_workbench_private_local import PEER, incoming, payload, protected, service
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.service import WeChatWorkbenchService


def model_service(ledger, generator):
    observer = service(ledger)
    wb = WeChatWorkbenchService(ledger, observer.receiver, local_drafts=True, private_model=True,
                               model="fixture-model", generator=generator)
    wb.reading = True
    return wb


def test_existing_message_one_model_call_edit_preview_and_no_send(tmp_path):
    async def run():
        calls = []

        async def generator(*args):
            calls.append(args)
            return Decision(ActionKind.REPLY, "模型草稿")

        with Ledger(tmp_path / "model.db") as ledger:
            wb = model_service(ledger, generator)
            source = incoming(ledger, age=86400)
            before = protected(ledger)
            request = payload(wb, source)
            del request["text"]
            assert wb.snapshot()["model"] == "fixture-model" and calls == []
            draft = await wb.generate(**request)
            await wb.generation_task
            assert (await wb.generate(**request))["draft_id"] == draft["draft_id"]
            assert len(calls) == 1 and calls[0][0] == source.text
            with pytest.raises(SocialError, match="already_used"):
                await wb.generate(**{**request, "request_id": "second-click"})
            wb.state.edit(draft["draft_id"], "edit", 1, "编辑后的草稿")
            preview = wb.preview(draft_id=draft["draft_id"], expected_version=2)
            assert preview["text"] == "编辑后的草稿" and preview["source_disposition"] == "stale_event"
            assert preview["preview_only"] and not preview["send_enabled"] and "confirmation_id" not in preview
            for op in (lambda: wb.prepare("p", draft["draft_id"], 2), lambda: wb.confirm("c", "any")):
                with pytest.raises(SocialError, match="private_readonly_scope_required"):
                    await op()
            assert protected(ledger) == before
            await wb.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("sender", [PEER.account.self_id, "wxid_unselected"])
def test_model_rejects_non_peer_before_inference(tmp_path, sender):
    async def run():
        calls = []

        async def generator(*args):
            calls.append(args)

        with Ledger(tmp_path / "wrong.db") as ledger:
            wb = model_service(ledger, generator)
            request = payload(wb, incoming(ledger, sender=sender))
            del request["text"]
            with pytest.raises(SocialError, match="private_peer_source_required"):
                await wb.generate(**request)
            assert calls == [] and not ledger._db.execute("SELECT 1 FROM workbench_drafts").fetchone()
            await wb.aclose()
    asyncio.run(run())


def test_failed_private_generation_is_not_retried(tmp_path):
    async def run():
        calls = []

        async def generator(*args):
            calls.append(args)
            raise SocialError("fixture_model_failure")

        with Ledger(tmp_path / "failed.db") as ledger:
            wb = model_service(ledger, generator)
            request = payload(wb, incoming(ledger))
            del request["text"]
            draft = await wb.generate(**request)
            await wb.generation_task
            assert (await wb.generate(**request))["state"] == "FAILED"
            assert len(calls) == 1 and wb.state.draft(draft["draft_id"])["model_calls"] == 1
            assert not ledger._db.execute("SELECT 1 FROM outbox").fetchone()
            await wb.aclose()
    asyncio.run(run())
