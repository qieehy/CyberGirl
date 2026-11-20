"""Subscription recovery, isolation, retry, cancellation and authenticated HTTP."""

import asyncio

import httpx
import pytest

from wechat_agent.social.models import SocialError
from wechat_agent.workbench.conversation_reads import ConversationReads
from wechat_agent.workbench.group_history import GroupHistory
from wechat_agent.workbench.history_console import HistoryConsoleServer


def history(tmp_path):
    h = GroupHistory(tmp_path, "a" * 64)
    calls = []

    async def load(kind, name, expected):
        calls.append((kind, name, expected))
        return dict(group=name, kind=kind, group_id_sha256="b" * 64,
                    account_fingerprint="a" * 64, messages=[dict(text=str(len(calls)))])

    h.conversation_loader = load
    return h, calls


def test_restore_separate_kinds_pause_remove_and_no_duplicates(tmp_path):
    async def run():
        h, calls = history(tmp_path)
        m = ConversationReads(h, interval=.01)
        await m.add("group", "same")
        await m.add("dm", "same")
        await m.add("group", "same")
        assert len(calls) == 2
        ids = list(m.entries)
        assert (await m.read(ids[0]))["messages"] != (await m.read(ids[1]))["messages"]
        m.start()
        await asyncio.sleep(.05)
        await m.control(ids[0], False)
        count = len([c for c in calls if c[0] == "group"])
        await asyncio.sleep(.04)
        assert len([c for c in calls if c[0] == "group"]) == count
        await m.aclose()
        restored = ConversationReads(h)
        assert not restored.entries[ids[0]]["enabled"]
        assert restored.entries[ids[1]]["enabled"]
        await restored.control(ids[0], False, True)
        assert ids[0] not in ConversationReads(h).entries
        assert all(expected == "b" * 64 for _, _, expected in calls[2:])
        await restored.aclose()
    asyncio.run(run())


def test_failure_keeps_cache_recovers_and_identity_cannot_replace(tmp_path):
    async def run():
        h, _ = history(tmp_path)
        m = ConversationReads(h, interval=.01)
        await m.add("dm", "peer")
        key = next(iter(m.entries))
        before = await m.read(key)

        async def wrong(*_):
            return dict(group="peer", kind="dm", group_id_sha256="c" * 64,
                        account_fingerprint="a" * 64, messages=[])

        h.conversation_loader = wrong
        m.start()
        await asyncio.sleep(.015)
        assert m.status[key]["last_error"] == "group_history_scope_mismatch"
        assert (await m.read(key))["messages"] == before["messages"]
        assert m.status[key]["next_read_at"]
        h.conversation_loader = history(tmp_path)[0].conversation_loader
        await asyncio.sleep(.05)
        assert m.status[key]["last_error"] is None
        await m.aclose()
    asyncio.run(run())


def test_close_cancels_inflight_and_restores_enabled_state(tmp_path):
    async def run():
        h, _ = history(tmp_path)
        m = ConversationReads(h)
        await m.add("group", "group")
        entered, closed = asyncio.Event(), asyncio.Event()

        async def blocked(*_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        h.conversation_loader = blocked
        m.start()
        await asyncio.wait_for(entered.wait(), 1)
        await m.aclose()
        assert closed.is_set() and not m.tasks
        assert next(iter(ConversationReads(h).entries.values()))["enabled"]
    asyncio.run(run())


def test_join_failure_not_persisted_and_account_manifest_isolation(tmp_path):
    async def run():
        h, _ = history(tmp_path)
        m = ConversationReads(h)

        async def failed(*_):
            raise SocialError("group_name_ambiguous")

        h.conversation_loader = failed
        with pytest.raises(SocialError, match="ambiguous"):
            await m.add("group", "same")
        assert not m.entries and not m.path.exists()
        h.conversation_loader = history(tmp_path)[0].conversation_loader
        await m.add("dm", "same")
        h.fingerprint = "c" * 64
        with pytest.raises(SocialError, match="scope_mismatch"):
            ConversationReads(h)
        await m.aclose()
    asyncio.run(run())


def test_only_one_manager_can_own_an_account_and_reload_after_handoff(tmp_path):
    async def run():
        h, _ = history(tmp_path)
        first, second = ConversationReads(h), ConversationReads(h)
        await first.add("group", "first")
        with pytest.raises(SocialError, match="read_manager_in_use"):
            await second.add("dm", "second")
        await first.aclose()
        await second.add("dm", "second")
        assert len(second.entries) == 2
        await second.aclose()
    asyncio.run(run())


def test_http_subscription_auth_origin_and_lifecycle(tmp_path):
    async def run():
        h, _ = history(tmp_path)
        server = HistoryConsoleServer(h, token="fixture")
        await server.start(0)
        try:
            async with httpx.AsyncClient(base_url=server.origin, trust_env=False) as client:
                path = "/api/wechat/reads/add"
                body = dict(kind="dm", name="peer")
                assert (await client.post(path, json=body, headers={"Origin":server.origin})).status_code == 401
                headers = {"Origin":server.origin,"Authorization":"Bearer fixture"}
                assert (await client.post(path, json=body, headers={**headers,"Origin":"http://elsewhere"})).status_code == 403
                result = await client.post(path, json=body, headers=headers)
                assert result.status_code == 200 and result.json()["read_only"]
                assert result.json()["model_calls"] == result.json()["send_calls"] == 0
                key = result.json()["subscriptions"][0]["id"]
                assert (await client.post("/api/wechat/reads/messages", json={"id":key},headers=headers)).json()["count"] == 1
        finally:
            await server.aclose()
        assert not server.reads.tasks
    asyncio.run(run())
