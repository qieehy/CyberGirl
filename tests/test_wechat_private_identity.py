"""Canonical page/root/account binding; selected labels never grant send authority."""

import hashlib
from types import SimpleNamespace

import pytest

from wechat_agent.bridges.wechat import private_identity as identity
from wechat_agent.bridges.wechat import qt_memory as qt
from wechat_agent.bridges.wechat import standalone_identity as standalone
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("failure", [None, "account", "input", "lineage", "layout", "root_token", "filehelper", "self", "group", "changed", "unstable"])
def test_selected_private_identity_requires_all_native_links(monkeypatch, failure):
    account = "account_self"
    username = {"filehelper": "filehelper", "self": account, "group": "123@chatroom"}.get(failure, "wxid_selected_peer")
    entries = [dict(class_name="mmui::ChatInputField"), dict(class_name="mmui::ChatMessagePage"),
               dict(class_name="mmui::ChatSingleWindow", meta_object_rva=standalone.SINGLE_META_RVA,
                    qobject_vtable_rva=standalone.SINGLE_VTABLE_RVA)]
    if failure == "input":
        entries[0]["class_name"] = "other"
    if failure == "layout":
        entries[-1]["meta_object_rva"] += 1

    class Reads:
        used = 100

        def __init__(self, memory):
            pass

        def stable(self):
            if failure == "unstable":
                raise SocialError("native_object_changed")

    monkeypatch.setattr(qt, "Reads", Reads)
    monkeypatch.setattr(qt, "accessible_interface", lambda *_: 20)
    monkeypatch.setattr(qt, "object_from_interface", lambda *_: (30, 0))
    monkeypatch.setattr(qt, "static_meta", lambda *_: entries[0])
    monkeypatch.setattr(qt, "parent_chain", lambda *_: (dict(complete=failure != "lineage", entries=entries), [30, 40, 50]))
    monkeypatch.setattr(qt, "_page_session_bytes", lambda *_: username.encode())
    calls = []

    def account_reader():
        calls.append(1)
        return "changed" if failure == "changed" and len(calls) > 1 else account

    def run():
        return identity.selected_private_identity(SimpleNamespace(base=100, validate=lambda: None), [3, 100],
            root_automation_sha256=hashlib.sha256(("ChatSingleWindow" + username).encode()).hexdigest() if failure != "root_token" else "0" * 64,
            account_reader=account_reader, expected_fingerprint=hashlib.sha256(account.encode()).hexdigest() if failure != "account" else "0" * 64)

    if failure:
        with pytest.raises(SocialError):
            run()
    else:
        value = run()
        assert value["username"] == username and value["stable"] and value["canonical_private_chat_verified"]
        assert not {"pointer", "runtime_ids", "send_allowed", "model_allowed"} & value.keys()
