"""Post-submit diagnostics grant no input and never serialize editor text."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts.probe_wechat_post_submit import audit_filehelper_cursor, shard_inventory
from scripts.wechat_native_worker import UiPipe
from tests.test_wechat_bridge import WINDOW
from tests.test_wechat_native_sender import setup
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.social.models import SocialError


def test_pattern_projection_keeps_exact_and_linebreak_differences_without_payloads():
    engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    run = subprocess.run([str(engine), "-NoProfile", "-NonInteractive", "-File",
                          str(Path(__file__).with_name("wechat_input_metadata_fixture.ps1"))], capture_output=True, timeout=15)
    assert run.returncode == 0
    data = json.loads(run.stdout)
    assert data[12]["exact_equal"] and not data[13]["exact_equal"]
    assert data[14]["trailing_linebreaks_only_difference"] and not data[14]["exact_equal"]
    assert data[15]["text_status"] == "unsupported" and data[15]["text_sha256"] is None
    assert data[16]["text_status"] == "too_long" and data[16]["text_length"] is None
    assert b"SYNTHETIC-DRAFT-DO-NOT-SERIALIZE" not in run.stdout


def pair(value):
    return dict(value_sha256=hashlib.sha256(value.encode()).hexdigest(), value_length=len(value), value_empty=not value,
                text_status="read", text_sha256=hashlib.sha256(b"").hexdigest(), text_length=0, text_empty=True,
                exact_equal=not value, trailing_linebreaks_only_difference=False)


def test_diagnostic_requires_native_checks_before_and_after_and_rejects_changed_draft(monkeypatch):
    backend, ui, checks = setup(monkeypatch)
    ui.text = "synthetic protected draft"
    ui.draft_patterns = lambda token: dict(input_token=token, comparison=pair(ui.text), send_enabled=True)
    result = backend.draft_diagnostics()
    assert result["native_identity_verified"] and len(checks) == 2 and not ui.calls
    assert ui.text not in str(result)

    def change(token):
        result = dict(input_token=token, comparison=pair(ui.text), send_enabled=True)
        ui.text += "changed"
        return result

    ui.draft_patterns = change
    with pytest.raises(SocialError, match="draft_changed_during_diagnostic"):
        backend.draft_diagnostics()
    assert not ui.calls


def test_readonly_transport_denies_all_effects_before_writing_a_frame(monkeypatch):
    commands = []

    class Child:
        stdin = io.BytesIO()
        stdout = io.BytesIO()

        def __init__(self, command, **kwargs):
            commands.append(command)

    monkeypatch.setattr(subprocess, "Popen", Child)
    pipe = UiPipe(WINDOW, read_only=True)
    assert commands[0][-1] == "-ReadOnly"
    for command in ("focus_once", "input_once", "submit_once"):
        with pytest.raises(SocialError, match="readonly_ui_command_denied"):
            pipe.rpc(command, token="synthetic")
    assert pipe.sequence == 0 and Child.stdin.getvalue() == b""


@pytest.mark.parametrize("check,expected", [("row_selection", "native_surface_row_selection"),
    ("button_offscreen", "native_surface_button_offscreen"),
    ("private-arbitrary-provider-value", "native_ui_rejected_at_94"), ([], "native_ui_rejected_at_94")])
def test_ui_failure_projects_only_allowlisted_surface_codes(check, expected):
    pipe = object.__new__(UiPipe)
    pipe.read_only, pipe.sequence = True, 0
    response = dict(error="native_ui_rejected", line=94, surface_check=check)
    pipe.process = type("Child", (), dict(stdin=io.BytesIO(), stdout=io.BytesIO(json.dumps(response).encode()+b"\n")))()
    with pytest.raises(SocialError) as caught:
        pipe.observe()
    assert caught.value.code == expected


def test_shard_inventory_reads_metadata_only_and_is_bounded(tmp_path):
    (tmp_path / "message_0.db").write_bytes(b"encrypted placeholder")
    (tmp_path / "message_2.db").write_bytes(b"other encrypted placeholder")
    (tmp_path / "message_2.db-wal").write_bytes(b"wal placeholder")
    (tmp_path / "unrelated-private-name").write_bytes(b"never read")
    result = shard_inventory(tmp_path)
    assert [row["shard"] for row in result] == ["message_0.db", "message_2.db"]
    assert result[1]["wal_present"] and result[1]["wal_size"] == 15
    assert "unrelated-private-name" not in str(result) and "placeholder" not in str(result)
    for index in range(129):
        (tmp_path / f"other-{index}").touch()
    with pytest.raises(SocialError, match="shard_directory_metadata_bound"):
        shard_inventory(tmp_path)


def test_cursor_audit_detects_new_local_ids_below_sort_watermark_without_reading_text():
    connection = sqlite3.connect(":memory:")
    table = "Msg_" + hashlib.md5(b"filehelper", usedforsecurity=False).hexdigest()
    connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
    connection.execute(f"CREATE TABLE {table}(local_id,local_type,real_sender_id,create_time,message_content,sort_seq)")
    connection.executemany(f"INSERT INTO {table} VALUES(?,1,1,0,'private body never read',?)", [(1, 100), (2, 101), (3, 0)])
    result = audit_filehelper_cursor(connection, Cursor(100, 1))
    assert result["max_local_id"] == 3 and result["new_local_ids_count"] == 2
    assert result["new_local_ids_below_sort_watermark"] == 1 and "private body" not in str(result)
    connection.close()


def test_submit_request_with_uncleared_draft_is_unknown_and_never_invoked_twice(monkeypatch):
    from tests.test_wechat_native_sender import ACTION

    backend, ui, _ = setup(monkeypatch)
    backend.post_submit_timeout = 0.01
    ui.clear_after_submit = False
    backend.prepare_input_focus()
    initial = backend.inspect()
    backend.input_text(ACTION.text, initial.input_token)
    with pytest.raises(SocialError, match="submission_called_but_draft_not_verified_empty"):
        backend.submit_once(initial.input_token)
    with pytest.raises(SocialError, match="native_submit_order_denied"):
        backend.submit_once(initial.input_token)
    assert ui.calls == ["focus", "input", "submit"] and ui.text == ACTION.text


def test_submit_can_observe_delayed_editor_clear_without_another_effect(monkeypatch):
    from tests.test_wechat_native_sender import ACTION

    backend, ui, _ = setup(monkeypatch)
    ui.clear_after_submit = False
    reads = []
    # A change during one observation is intentionally rejected by the native
    # gate. Simulate the clear between complete observations instead.
    original_diagnostic = backend.draft_diagnostics

    def diagnostic(**kwargs):
        if reads:
            ui.text = ""
        reads.append(1)
        return original_diagnostic(**kwargs)

    backend.draft_diagnostics = diagnostic
    backend.prepare_input_focus()
    initial = backend.inspect()
    backend.input_text(ACTION.text, initial.input_token)
    backend.submit_once(initial.input_token)
    assert len(reads) == 2 and ui.calls == ["focus", "input", "submit"]


@pytest.mark.parametrize("method", [0x9EA590, 0x123456])
def test_unverified_or_focus_only_button_is_rejected_before_focus_or_input(monkeypatch, method):
    from wechat_agent.bridges.wechat.native_sender import NativeSendBackend

    backend, ui, _ = setup(monkeypatch)
    backend._require_submit_path = lambda: NativeSendBackend._require_submit_path(backend)
    backend.button_diagnostics = lambda: dict(interface_cast_rva=0x9EA840, methods=[dict(offset=32, function_rva=method)])
    with pytest.raises(SocialError, match="native_button_press_not_implemented|native_button_submission_path_unverified"):
        backend.prepare_input_focus()
    assert not ui.calls and ui.text == ""
