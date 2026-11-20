"""Critical exact-group/limit/offline/scope/API checks, with no native process."""

import asyncio
import hashlib
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from wechat_agent.social.models import SocialError
from wechat_agent.workbench.group_history import GroupHistory
from wechat_agent.workbench.group_history_reader import read_selected_group
from wechat_agent.workbench.history_console import HistoryConsoleServer


def record(group="KG牛牛俱乐部"):
    return dict(
        group=group,
        group_id_sha256="b" * 64,
        account_fingerprint="a" * 64,
        messages=[
            dict(
                number=1,
                time="2026-11-17",
                sender="fixture",
                text="<script>fixture</script>",
                type=1,
            )
        ],
        count=1,
    )


@pytest.mark.parametrize("kind,username", [("group", "selected@chatroom"), ("dm", "selected-peer")])
def test_exact_group_query_reads_latest_limit_and_no_other_conversation(tmp_path, kind, username):
    folder = tmp_path / "db_storage/contact"
    folder.mkdir(parents=True)
    (folder / "contact.db").touch()
    contact = sqlite3.connect(":memory:")
    contact.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)")
    contact.executemany(
        "INSERT INTO contact VALUES(?,?,?)",
        [
            (username, "KG牛牛俱乐部", ""),
            ("other@chatroom", "其他群", ""),
            ("member", "成员", ""),
        ],
    )
    data = sqlite3.connect(":memory:")
    data.execute("CREATE TABLE Name2Id(user_name TEXT)")
    data.execute("INSERT INTO Name2Id VALUES('member')")
    table = "Msg_" + hashlib.md5(username.encode(), usedforsecurity=False).hexdigest()
    data.execute(
        'CREATE TABLE "'
        + table
        + '"(local_id INTEGER PRIMARY KEY,sort_seq INTEGER,create_time INTEGER,local_type INTEGER,real_sender_id INTEGER,message_content TEXT)'
    )
    data.executemany(
        'INSERT INTO "' + table + '" VALUES(?,?,?,1,1,?)',
        [(n, n, 1800000000 + n, "text " + str(n)) for n in range(1, 251)],
    )
    source = SimpleNamespace(
        account=tmp_path,
        username="self-account",
        shards={"message_0.db": ("path", None, None, None)},
        manifest=lambda: None,
        validate=lambda: None,
    )
    result = read_selected_group(source, lambda _: contact, lambda *_: data, "KG牛牛俱乐部", 200, kind=kind)
    assert (
        result["count"] == 200
        and result["messages"][0]["text"] == "text 51"
        and result["messages"][-1]["text"] == "text 250"
    )
    assert (
        source.target_username == username
        and result["send_calls"] == result["model_calls"] == 0
    )
    with pytest.raises(SocialError, match="scope_mismatch"):
        read_selected_group(source, lambda _: contact, lambda *_: data, "KG牛牛俱乐部", 200,
                            kind=kind, expected_id="0" * 64)
    contact.execute("INSERT INTO contact VALUES(?, 'KG牛牛俱乐部', '')", ("duplicate@chatroom" if kind == "group" else "duplicate-peer",))
    with pytest.raises(SocialError, match="group_name_ambiguous"):
        read_selected_group(source, lambda _: contact, lambda *_: data, "KG牛牛俱乐部", 200, kind=kind)
    contact.close()
    data.close()


def test_offline_cache_and_failed_refresh_preserve_saved_messages(tmp_path):
    async def run():
        calls = []

        async def unavailable(*args):
            calls.append(args)
            raise SocialError("group_history_unavailable")

        history = GroupHistory(tmp_path, "a" * 64, unavailable)
        history.save(record())
        saved = await history.read("KG牛牛俱乐部")
        assert saved["cached"] and not calls
        refreshed = await history.read("KG牛牛俱乐部", refresh=True)
        assert refreshed["cached"] and refreshed["refresh_error"] == "group_history_unavailable"
        assert refreshed["messages"] == saved["messages"]

    asyncio.run(run())


def test_wrong_account_or_group_cannot_replace_cache(tmp_path):
    async def run():
        async def wrong(*_):
            return record("another group")

        history = GroupHistory(tmp_path, "a" * 64, wrong)
        history.save(record())
        with pytest.raises(SocialError, match="scope_mismatch"):
            await history.read("KG牛牛俱乐部", refresh=True)
        with pytest.raises(SocialError, match="scope_mismatch"):
            history.save({**record(), "account_fingerprint": "c" * 64})
        with pytest.raises(SocialError, match="invalid_group_history_request"):
            await history.read("../private", 201)

    asyncio.run(run())


def test_history_http_requires_auth_and_has_no_send_route(tmp_path):
    async def run():
        history = GroupHistory(tmp_path, "a" * 64)
        history.save(record())
        server = HistoryConsoleServer(history, token="fixture")
        await server.start(0)
        try:
            async with httpx.AsyncClient(base_url=server.origin, trust_env=False) as client:
                body = dict(group="KG牛牛俱乐部", limit=200, refresh=False)
                headers = {"Origin": server.origin, "Authorization": "Bearer fixture"}
                assert (
                    await client.post(
                        "/api/wechat/group-history", json=body, headers={"Origin": server.origin}
                    )
                ).status_code == 401
                reply = await client.post("/api/wechat/group-history", json=body, headers=headers)
                assert reply.status_code == 200 and reply.json()["read_only"]
                assert (
                    await client.post("/api/wechat/private-send", json={}, headers=headers)
                ).status_code == 404
        finally:
            await server.aclose()

    asyncio.run(run())
