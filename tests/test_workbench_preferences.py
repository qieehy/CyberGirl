"""Configuration and actual pre-model rules, with synthetic delivery only."""

import asyncio
from copy import deepcopy

import pytest

from tests.test_private_auto_reply import build
from tests.test_workbench_private_local import incoming
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.preferences import DEFAULTS, match_rule, validate_config


def rule(action="reply"):
    return dict(
        id="greeting",
        name="打招呼",
        enabled=True,
        match="contains",
        pattern="FIXTURE",
        action=action,
        reply="规则回复" if action == "reply" else "",
        case_sensitive=False,
    )


@pytest.mark.parametrize("action", ["reply", "ignore"])
def test_saved_rules_process_fresh_inbound_without_model_or_replay(tmp_path, action):
    async def run():
        async def model(*_):
            pytest.fail("a matched rule must not call the model")

        path = tmp_path / "rules.db"
        with Ledger(path) as ledger:
            wb, sender = build(ledger, model)
            server = WorkbenchConsoleServer(ledger, wb)
            await server._route(
                "POST", "/api/wechat/rules", dict(expected_revision=0, rules=[rule(action)])
            )
            preview = await server._route(
                "POST", "/api/wechat/rules/preview", dict(text="a fixture message")
            )
            assert preview["preview_only"] and preview["matched"]["id"] == "greeting"
            assert sender.calls == []
            wb.auto_reply.set_enabled(True)
            incoming(ledger)
            wb.auto_reply.tick()
            await wb.auto_reply.task
            wb.auto_reply.tick()
            assert len(sender.calls) == (1 if action == "reply" else 0)
            row = ledger._db.execute(
                "SELECT model,model_calls,state FROM workbench_drafts"
            ).fetchone()
            assert row["model"] == "local-rule" and row["model_calls"] == 0
            assert row["state"] == ("SUBMITTED" if action == "reply" else "SILENT")
            data = await server._route("GET", "/api/wechat/logs", None)
            assert data["stats"]["model_calls_today"] == 0
            assert any(e["kind"] == "规则" for e in data["events"])
            await wb.aclose()
        with Ledger(path) as ledger:
            assert (
                ledger._db.execute("SELECT revision FROM workbench_preferences").fetchone()[0] == 1
            )

    asyncio.run(run())


def test_settings_cas_validation_and_disabled_media_are_real(tmp_path):
    async def run():
        async def model(*_):
            pytest.fail("disabled media must not enter the model")

        with Ledger(tmp_path / "config.db") as ledger:
            wb, sender = build(ledger, model)
            config = {**DEFAULTS, "voice_enabled": False, "context_pairs": 0, "daily_budget": 1}
            wb.preferences.save(0, config=config)
            with pytest.raises(SocialError, match="version_conflict"):
                wb.preferences.save(0, config=config)
            for bad in (
                {**config, "daily_budget": 31},
                {**config, "context_pairs": True},
                {**config, "extra": 1},
            ):
                with pytest.raises(SocialError, match="invalid_config"):
                    validate_config(bad)
            wb.auto_reply.set_enabled(True)
            incoming(ledger, kind="voice")
            wb.auto_reply.tick()
            await wb.auto_reply.task
            assert wb.auto_reply.enabled and sender.calls == []
            assert (
                ledger._db.execute("SELECT model_calls,state FROM workbench_drafts").fetchone()[
                    "state"
                ]
                == "SILENT"
            )
            await wb.aclose()

    asyncio.run(run())


def test_daily_budget_applies_and_local_rule_still_works_when_budget_used(tmp_path):
    async def run():
        async def model(*_):
            return Decision(ActionKind.REPLY, "模型回复")

        with Ledger(tmp_path / "budget.db") as ledger:
            wb, _ = build(ledger, model)
            wb.preferences.save(0, config={**DEFAULTS, "daily_budget": 1})
            first = incoming(ledger)

            def args(source, key):
                return dict(
                    request_id=key,
                    source_message_id=source.message_id,
                    expected_message_revision=wb.state.revision(),
                    expected_persona_version=1,
                )

            await wb.generate(**args(first, "first"))
            await wb.generation_task
            second = incoming(ledger, n=2)
            with pytest.raises(SocialError, match="model_daily_budget"):
                await wb.generate(**args(second, "second"))
            wb.preferences.save(1, rules=[rule()])
            await wb.generate(**args(second, "rule"))
            await wb.generation_task
            assert (
                ledger._db.execute(
                    "SELECT COUNT(*) FROM workbench_drafts WHERE model_calls=1"
                ).fetchone()[0]
                == 1
            )
            await wb.aclose()

    asyncio.run(run())


def test_rule_priority_case_and_preview_do_not_change_rules():
    first, second = rule("ignore"), rule()
    second["id"] = "second"
    rules = [first, second]
    saved = deepcopy(rules)
    assert match_rule(rules, "fixture")["action"] == "ignore"
    first["enabled"] = False
    assert match_rule(rules, "fixture")["id"] == "second"
    second["case_sensitive"] = True
    assert match_rule(rules, "fixture") is None
    assert saved[0]["enabled"] is True
