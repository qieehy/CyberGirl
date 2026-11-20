"""Critical managed-memory and proactive fences; synthetic model/sender only."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_private_auto_reply import build
from tests.test_workbench_private_local import incoming
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.companion import Companion, daily_calls
from wechat_agent.workbench.service import LiveDraftGenerator


class Model:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = None

    async def __call__(self, *_):
        return Decision(ActionKind.REPLY, "普通回复")

    async def companion_turn(self, kind, material):
        self.calls.append((kind, material))
        self.entered.set()
        if self.release:
            await self.release.wait()
        if kind == "summary":
            return {
                "memories": [
                    {
                        "content": "用户希望记住这段对话",
                        "source_ids": [material["timeline"][0]["id"]],
                    }
                ]
            }
        return {"text": "今天过得怎么样？"}


def setup(ledger):
    model = Model()
    wb, sender = build(ledger, model)
    wb.auto_reply.set_enabled(True)
    return wb, sender, model


def settings(companion, **changes):
    s = companion.settings()
    s["config"].update(changes)
    companion.save(s["revision"], s["config"], s["profile"], s["role"])


def test_memory_crud_scope_recall_and_forget(tmp_path):
    with Ledger(tmp_path / "memory.db") as ledger:
        wb, _, _ = setup(ledger)
        c = wb.companion
        c.put_memory(content="用户喜欢喝茉莉花茶", kind="preference", importance=80)
        memory = c.memories()[0]
        assert c.recall("喝什么花茶")[0]["id"] == memory["id"]
        assert "花茶" in str(c.context("花茶"))
        with ledger._transaction() as db:
            db.execute(
                "INSERT INTO companion_memories VALUES('other','other-scope',1,'其他人秘密','fact',100,1,'operator',0,0,0)"
            )
        assert "其他人秘密" not in str(c.context("其他人秘密"))
        c.forget(memory["id"], memory["version"])
        assert not c.recall("花茶")
        deleted = c.memories(deleted=True)[0]
        c.forget(deleted["id"], deleted["version"], restore=True)
        assert c.recall("花茶")
        with pytest.raises(SocialError, match="not_found"):
            c.forget("other", 1)
        settings(c, recall_enabled=False, companion_enabled=False)
        assert c.context("花茶") == {}


def test_capture_new_verified_only_and_explicit_memory(tmp_path):
    with Ledger(tmp_path / "capture.db") as ledger:
        wb, _, _ = setup(ledger)
        c = wb.companion
        source = incoming(ledger)
        with ledger._transaction() as db:
            db.execute(
                "UPDATE bridge_message_payloads SET text='记住：下周面试' WHERE message_id=?",
                (source.message_id,),
            )
        c.capture()
        c.capture()
        assert c.memories()[0]["content"] == "下周面试"
        assert c.snapshot()["timeline_count"] == 1
        incoming(ledger, n=2, sender=wb.binding.account.self_id)
        c.capture()
        assert c.snapshot()["timeline_count"] == 1
        settings(c, capture_enabled=False)
        incoming(ledger, n=3)
        c.capture()
        settings(c, capture_enabled=True)
        c.capture()
        assert c.snapshot()["timeline_count"] == 1


def test_summary_source_validation_and_shared_budget(tmp_path):
    async def run():
        with Ledger(tmp_path / "summary.db") as ledger:
            wb, _, model = setup(ledger)
            c = wb.companion
            c.record("source", "user", "请记一下我的面试", ledger.now())
            await c._run(
                "summary",
                [dict(id="source", text="请记一下我的面试", role="user", at=ledger.now())],
            )
            assert model.calls and any(r["kind"] == "summary" for r in c.memories())
            assert daily_calls(ledger._db, ledger.now()) == 1
            with pytest.raises(SocialError, match="invalid_companion_summary"):
                c._summary(
                    "bad", {"memories": [{"content": "无来源", "source_ids": ["invented"]}]}, []
                )
            await wb.aclose()

    asyncio.run(run())


def test_pending_memory_change_cancels_summary(tmp_path):
    async def run():
        with Ledger(tmp_path / "cancel.db") as ledger:
            wb, _, model = setup(ledger)
            model.release = asyncio.Event()
            c = wb.companion
            task = asyncio.create_task(
                c._run("summary", [dict(id="s", text="原始消息", role="user", at=ledger.now())])
            )
            await model.entered.wait()
            c.put_memory(content="只保留这个人工修正")
            model.release.set()
            await task
            assert [r["content"] for r in c.memories()] == ["只保留这个人工修正"]
            assert c.snapshot()["jobs"][0]["state"] == "FAILED"
            await wb.aclose()

    asyncio.run(run())


def test_proactive_quiet_idle_and_daily_gates(tmp_path):
    clock = [1800000000.0]
    with Ledger(tmp_path / "gates.db", now=lambda: clock[0]) as ledger:
        wb, _, _ = setup(ledger)
        c = wb.companion
        assert c.eligible() == "proactive_disabled"
        settings(c, proactive_enabled=True, idle_minutes=10, quiet_start="00:00", quiet_end="00:00")
        assert c.eligible() == "waiting_new_conversation"
        c.record("s", "user", "自然聊天", clock[0])
        assert c.eligible() == "idle_wait"
        clock[0] += 601
        assert c.eligible() is None
        wb.auto_reply.set_enabled(False)
        assert c.eligible() == "auto_reply_paused"


@pytest.mark.parametrize("stop", [False, True])
def test_proactive_single_send_or_pause_during_model(tmp_path, stop):
    async def run():
        clock = [1800000000.0]
        with Ledger(tmp_path / "send.db", now=lambda: clock[0]) as ledger:
            wb, _, model = setup(ledger)
            sent = []

            async def send(request_id, text, *, guard=None):
                if guard:
                    guard()
                sent.append((request_id, text))
                return dict(action_id="fixture-action", local_echo=True)

            wb.private_sender = SimpleNamespace(check_ready=lambda: None, send=send)
            c = wb.companion
            settings(
                c, proactive_enabled=True, idle_minutes=10, quiet_start="00:00", quiet_end="00:00"
            )
            c.record("s", "user", "自然聊天", clock[0])
            clock[0] += 601
            model.release = asyncio.Event()
            c.tick()
            await model.entered.wait()
            if stop:
                wb.auto_reply.set_enabled(False)
            model.release.set()
            await c.task
            c.tick()
            assert len(sent) == (0 if stop else 1)
            assert len(model.calls) == 1
            if not stop:
                assert c.eligible() == "interval_wait"
            await wb.aclose()

    asyncio.run(run())


def test_settings_validation_and_stale_edit(tmp_path):
    with Ledger(tmp_path / "validate.db") as ledger:
        wb, _, _ = setup(ledger)
        c = wb.companion
        s = c.settings()
        with pytest.raises(SocialError, match="invalid_companion_data"):
            c.save(s["revision"], dict(s["config"], idle_minutes=True), s["profile"], s["role"])
        c.put_memory(content="手动记忆")
        with pytest.raises(SocialError, match="version_conflict"):
            c.save(s["revision"], s["config"], s["profile"], s["role"])


def test_stop_is_independent_of_editor_data_and_keeps_memories(tmp_path):
    with Ledger(tmp_path / "stop.db") as ledger:
        wb, _, _ = setup(ledger)
        c = wb.companion
        c.put_memory(content="保留的人物事实")
        snapshot = c.stop()
        assert all(
            snapshot["config"][key] is False
            for key in (
                "memory_enabled",
                "capture_enabled",
                "recall_enabled",
                "companion_enabled",
                "summary_enabled",
                "proactive_enabled",
            )
        )
        assert c.context("人物") == {} and len(c.memories()) == 1


def test_restart_keeps_unknown_without_replaying_sources(tmp_path):
    with Ledger(tmp_path / "restart.db") as ledger:
        wb, _, model = setup(ledger)
        c = wb.companion
        settings(c, proactive_enabled=True)
        c.record("source", "user", "未总结原文", ledger.now())
        with ledger._transaction() as db:
            db.execute(
                "INSERT INTO companion_jobs(id,scope,kind,state,created_at) VALUES('summary',?,'summary','GENERATING',?)",
                (c.scope, ledger.now()),
            )
            db.execute(
                "INSERT INTO companion_jobs(id,scope,kind,state,created_at) VALUES('send',?,'proactive','SENDING',?)",
                (c.scope, ledger.now()),
            )
            db.execute("INSERT INTO companion_job_sources VALUES('summary','source')")
        restored = Companion(wb)
        assert {r["state"] for r in restored.snapshot()["jobs"]} == {"UNKNOWN"}
        assert restored.settings()["config"]["proactive_enabled"] is False
        assert (
            ledger._db.execute(
                "SELECT summarized FROM companion_timeline WHERE id='source'"
            ).fetchone()[0]
            == -1
        )
        restored.tick()
        assert model.calls == []


def test_real_generator_adapter_injects_memory_without_changing_incoming(tmp_path):
    async def run():
        with Ledger(tmp_path / "prompt.db") as ledger:
            wb, _, _ = setup(ledger)
            wb.companion.put_memory(content="喜欢茉莉花茶", pinned=True)
            calls = []
            provider = SimpleNamespace(http_attempts=0)

            async def session():
                return "fixture"

            async def turn(scope, content, deliver):
                data = json.loads(content)
                calls.append(data)
                assert "茉莉花茶" in str(data["COMPANION_CONTEXT_JSON"])
                assert data["incoming_text"] == "fixture source"
                provider.http_attempts += 1
                return SimpleNamespace(
                    draft=SimpleNamespace(text='{"action":"REPLY","reply_text":"模型回复"}')
                )

            @asynccontextmanager
            async def application(*_):
                yield SimpleNamespace(
                    provider=provider,
                    engine=SimpleNamespace(turn=turn),
                    tokens=SimpleNamespace(context_key=lambda: "fixture", get_session=session),
                )

            async def catalog(_):
                return SimpleNamespace(contains=lambda _: True, context_key="fixture")

            wb.generator = LiveDraftGenerator(
                ledger, "fixture", Path("unused"), app_factory=application, catalog_fetch=catalog
            )
            wb.generator.companion = wb.companion
            source = incoming(ledger)
            result = await wb.generate(
                request_id="memory-prompt",
                source_message_id=source.message_id,
                expected_message_revision=wb.state.revision(),
                expected_persona_version=1,
            )
            await wb.generation_task
            assert len(calls) == 1 and wb.state.draft(result["draft_id"])["state"] == "READY"
            await wb.aclose()

    asyncio.run(run())
