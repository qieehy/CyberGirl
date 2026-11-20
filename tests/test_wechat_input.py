"""Read-only capability projection and collector lifecycle, no live desktop."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import probe_wechat_input as probe
from tests.test_wechat_bridge import WINDOW


def test_powershell_projection_preserves_capabilities_without_raw_values():
    engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    run = subprocess.run([str(engine), "-NoProfile", "-NonInteractive", "-File",
                          str(Path(__file__).with_name("wechat_input_metadata_fixture.ps1"))],
                         capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr.decode(errors="replace")
    data = json.loads(run.stdout)
    assert data[0]["role"] == "chat_input" and data[1]["role"] == "session_list"
    assert data[2]["automation_id_filehelper_token"] and not data[3]["automation_id_filehelper_token"]
    assert data[3]["display_is_filehelper"]  # Display equality never proves identity.
    assert data[4]["role"] == "send_button_candidate"
    assert data[5]["empty"] and data[5]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert data[6]["sha256"] == hashlib.sha256(b"SYNTHETIC-DRAFT-DO-NOT-SERIALIZE").hexdigest()
    assert data[7]["status"] == "too_long"
    assert data[8]["role"] == "chat_info_candidate"
    assert data[9]["automation_id_filehelper_md5"] and not data[10]["automation_id_filehelper_md5"]
    assert data[11]["marker_present"] and data[11]["shape"]["non_ascii_count"] == 6
    assert not {"separators", "payload", "codepoints"} & data[11]["shape"].keys()
    assert b"SYNTHETIC-DRAFT-DO-NOT-SERIALIZE" not in run.stdout
    assert all(data[17].values()) and not any(data[18].values())
    assert set(data[17]) == {"input_name_match", "row_name_match", "input_id_match", "input_enabled"}
    assert all(type(v) is bool for v in data[18].values())


def test_capability_hints_truncation_and_wrong_window_never_grant_send_permission():
    node = dict(index=0, role="chat_input", automation_id_filehelper_token=True, selected=False,
                help_is_canonical_filehelper=True)
    data = dict(status="observed", pid=WINDOW.pid, hwnd=WINDOW.hwnd,
                created_filetime=str(WINDOW.created_filetime), tree=dict(nodes=[node], bounded=False, errors=[]))
    result = probe.assess(data, WINDOW)
    assert result["complete"] and result["identity_hint_indexes"] == [0]
    assert not result["canonical_identity_verified"] and not result["native_send_enabled"]
    data["tree"]["bounded"] = True
    assert not probe.assess(data, WINDOW)["complete"]
    data["tree"]["bounded"], data["tree"]["errors"] = False, [{"type": "synthetic"}]
    assert not probe.assess(data, WINDOW)["complete"]
    data["created_filetime"] = "999"
    with pytest.raises(ValueError, match="binding mismatch"):
        probe.assess(data, WINDOW)


def test_collector_timeout_kills_before_return_and_unreaped_exit_is_distinct(tmp_path, monkeypatch):
    events = []

    class Child:
        pid = 789
        returncode = None
        reaped = True

        def __init__(self, command, **kwargs):
            assert "-TargetHwnd" in command and kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW

        def wait(self, timeout):
            events.append(("wait", timeout))
            if timeout == 25 or not self.reaped:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            self.returncode = 1

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("kill")

    class Job:
        def __init__(self, pid):
            events.append("job")

        def close(self):
            events.append("close_job")

    monkeypatch.setattr(probe.subprocess, "Popen", Child)
    monkeypatch.setattr(probe, "KillJob", Job)
    with pytest.raises(subprocess.TimeoutExpired):
        probe.collect(WINDOW, tmp_path / "not-written.json")
    assert events == ["job", ("wait", 25), "close_job", "kill", ("wait", 2)]
    Child.reaped = False
    with pytest.raises(probe.CollectorNotReaped):
        probe.collect(WINDOW, tmp_path / "not-written.json")


def test_runtime_ids_use_only_transient_pipe_and_are_bound_to_selected_session(tmp_path, monkeypatch):
    output = tmp_path / "metadata.json"
    node = dict(index=0, role="other", automation_id_filehelper_token=False,
                selected=True, class_name="mmui::ChatSessionCell", name_contains_filehelper_label=True)
    input_node = dict(index=1, role="chat_input", automation_id_filehelper_token=False,
                      selected=False, class_name="mmui::ChatInputField", display_is_filehelper=True)
    data = dict(status="observed", pid=WINDOW.pid, hwnd=WINDOW.hwnd,
                created_filetime=str(WINDOW.created_filetime), tree=dict(nodes=[node,input_node], bounded=False, errors=[]))
    output.write_text(json.dumps(data))
    transient = dict(pid=WINDOW.pid, hwnd=WINDOW.hwnd, created_filetime=str(WINDOW.created_filetime),
                     runtime_ids={"selected_session": [3, -2147483647]})

    class Child:
        pid = 789
        returncode = 0

        def __init__(self, command, **kwargs):
            assert command[-1] == "-RuntimePipe" and kwargs["stdout"] == subprocess.PIPE

        def communicate(self, timeout):
            return json.dumps(transient).encode(), None

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class Job:
        def __init__(self, pid):
            pass

        def close(self):
            pass

    monkeypatch.setattr(probe.subprocess, "Popen", Child)
    monkeypatch.setattr(probe, "KillJob", Job)
    received = []
    result = probe.collect(WINDOW, output, runtime_callback=lambda ids: received.append(ids) or {"stable": True})
    assert received == [transient["runtime_ids"]] and result["native_object"] == {"stable": True}
    assert not result["canonical_identity_verified"] and not result["native_send_enabled"]
    canonical = probe.collect(WINDOW, output, runtime_callback=lambda _: dict(
        stable=True, canonical_conversation_verified=True, matched_database_account=True))
    assert canonical["canonical_identity_verified"] and not canonical["native_send_enabled"]
    assert "-2147483647" not in output.read_text() and "runtime_ids" not in result
    data["tree"]["bounded"] = True
    output.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="native session observation incomplete"):
        probe.collect(WINDOW, output, runtime_callback=lambda _: pytest.fail("incomplete tree must not reach native lookup"))
    data["tree"]["bounded"] = False
    output.write_text(json.dumps(data))
    input_node["display_is_filehelper"] = False
    output.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="not the filehelper research candidate"):
        probe.collect(WINDOW, output, runtime_callback=lambda _: pytest.fail("other current chats must not reach native lookup"))
    input_node["display_is_filehelper"] = True
    output.write_text(json.dumps(data))
    transient["created_filetime"] = "999"
    with pytest.raises(ValueError, match="runtime pipe binding mismatch"):
        probe.collect(WINDOW, output, runtime_callback=lambda _: pytest.fail("stale pipe must not reach memory lookup"))
