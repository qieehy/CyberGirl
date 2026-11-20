import hashlib
import io
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.wechat_standalone_worker import StandaloneSession
from tests.test_wechat_background_control import ACTION, BINDING
from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat import standalone_identity as identity
from wechat_agent.bridges.wechat.background_contract import CONTROL_MARKER, CONTROL_REQUEST
from wechat_agent.bridges.wechat.background_process import BackgroundIntentClient
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def native_fixture(monkeypatch):
    state = {"page_matches": True, "stable": True}
    entries = [dict(class_name="mmui::ChatInputField"),
               dict(class_name="mmui::ChatMessagePage"),
               dict(class_name="mmui::ChatSingleWindow", meta_object_rva=identity.SINGLE_META_RVA,
                    qobject_vtable_rva=identity.SINGLE_VTABLE_RVA)]
    memory = SimpleNamespace(base=100, validate=lambda: None)

    class Reads:
        used = 100

        def __init__(self, memory):
            pass

        def stable(self):
            if not state["stable"]:
                raise SocialError("native_object_changed")

    monkeypatch.setattr(identity, "Reads", Reads)
    monkeypatch.setattr(identity, "accessible_interface", lambda *_: 20)
    monkeypatch.setattr(identity, "object_from_interface", lambda *_: (30, 0))
    monkeypatch.setattr(identity, "static_meta", lambda *_: entries[0])
    monkeypatch.setattr(identity, "parent_chain", lambda *_: ({"complete": True, "entries": entries}, [30, 40, 50]))
    monkeypatch.setattr(identity, "page_session_key", lambda *_: {"matches_filehelper": state["page_matches"]})
    return memory, entries, state


@pytest.mark.parametrize("failure", [None, "page", "root", "layout", "account", "stable"])
def test_native_standalone_requires_canonical_page_and_pinned_root(monkeypatch, failure):
    memory, entries, state = native_fixture(monkeypatch)
    account = "fixture"
    fingerprint = hashlib.sha256(account.encode()).hexdigest()
    if failure == "page":
        state["page_matches"] = False
    elif failure == "root":
        entries[-1]["class_name"] = "mmui::MainWindow"
    elif failure == "layout":
        entries[-1]["meta_object_rva"] += 1
    elif failure == "account":
        account = "other"
    elif failure == "stable":
        state["stable"] = False

    def run():
        return identity.verify_standalone_filehelper(memory, [3, 0x80000001],
            account_reader=lambda: account, expected_fingerprint=fingerprint)

    if failure:
        with pytest.raises(SocialError):
            run()
    else:
        result = run()
        assert result["canonical_conversation_verified"] and result["stable"]
        assert not {"pointer", "username", "runtime_ids"} & result.keys()


@pytest.mark.parametrize("bad", [None, "operation", "ordinal", "stage", "accepted"])
def test_background_ack_is_bound_to_exact_operation_stage_and_ordinal(bad):
    ack = dict(operation_id="op", sequence=2,
               background_ack=dict(stage="CHAR_INTENT", ordinal=1, accepted=True))
    if bad == "operation":
        ack["operation_id"] = "different"
    elif bad == "ordinal":
        ack["background_ack"]["ordinal"] = True
    elif bad == "stage":
        ack["background_ack"]["stage"] = "UP_INTENT"
    elif bad == "accepted":
        ack["background_ack"]["accepted"] = 1
    client = BackgroundIntentClient(io.BytesIO(json.dumps(ack).encode() + b"\n"), io.BytesIO(), "op", 2)
    if bad:
        with pytest.raises(SocialError, match="ack_invalid"):
            client.record("CHAR_INTENT")
    else:
        assert client.record("CHAR_INTENT") is True


def test_readonly_standalone_worker_cannot_run_control_after_prepare():
    session = StandaloneSession(BINDING.account.window, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest())
    request = dict(version=1, operation_id="readonly", sequence=1, command="init",
                   binding=asdict(BINDING), action=asdict(ACTION))
    session.handle(request, io.BytesIO(), io.BytesIO())
    session.capabilities = object()
    with pytest.raises(SocialError, match="not_permitted"):
        session.handle(dict(version=1, operation_id="readonly", sequence=2, command="run_background"),
                       io.BytesIO(), io.BytesIO())


def test_background_review_does_not_fabricate_foreground_facts_or_unblock(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        facts.pop("foreground_hit_and_focus")
        facts.pop("no_capture_or_hardware_input")
        facts.update(standalone_identity_verified=True, background_observer_active=True, no_capture=True)
        review = channel_review.authorize(ledger, binding, CONTROL_REQUEST, CONTROL_MARKER, facts,
            approval_ref="synthetic-background-approval", backend_kind="standalone_background_control")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
        action = SendState(ledger).queue_marker(binding, CONTROL_REQUEST, CONTROL_MARKER, review_id=review)
        assert ledger.action_summary(action)["state"] == "QUEUED"


@pytest.mark.parametrize("missing", ["standalone_identity_verified", "background_observer_active", "no_capture"])
def test_background_review_requires_real_background_evidence(tmp_path, missing):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        facts.pop("foreground_hit_and_focus")
        facts.pop("no_capture_or_hardware_input")
        facts.update(standalone_identity_verified=True, background_observer_active=True, no_capture=True)
        facts[missing] = False
        with pytest.raises(SocialError):
            channel_review.authorize(ledger, binding, CONTROL_REQUEST, CONTROL_MARKER, facts,
                approval_ref="synthetic-background-approval", backend_kind="standalone_background_control")


@pytest.mark.skipif(os.name != "nt", reason="owned hidden Windows queue fixture")
@pytest.mark.parametrize("mode", ["route", "destroy"])
def test_real_hwnd_queue_isolation_and_destroyed_window_does_not_redirect_to_replacement(mode):
    result = subprocess.run(python_worker_command(Path(__file__).with_name("wechat_standalone_lifetime.py"), mode),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert not data["external_hwnds_used"] and not data["windows_visible"]
    assert ["main-other-conversation", ord("M")] in data["received"]
    if mode == "route":
        assert ["fixed-filehelper", ord("F")] in data["received"]
    else:
        assert ["replacement", ord("R")] in data["received"]
        assert all(item[1] != ord("F") for item in data["received"])
