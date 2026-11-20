"""Read-only real-observer boundary and lossy diagnostics; synthetic UI only."""

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wechat_directed_click import snapshot
from tests.test_wechat_native_sender import ACTION, BINDING, setup
from wechat_agent.bridges.wechat.click_geometry import geometry_report, parse_geometry
from wechat_agent.bridges.wechat.send_worker import WorkerSession
from wechat_agent.social.models import SocialError


def raw_geometry():
    before, _ = snapshot()
    data = asdict(before)
    data.pop("input")
    data.update(input_token=before.input.input_token, button_bounds=[500, 400, 600, 440],
                client_bounds=[0, 0, 640, 480], client_point=[550, 420], dpi_context_restored=True)
    return before.input, data


def test_readonly_nonempty_draft_has_no_initial_grant_even_when_geometry_matches():
    observation, data = raw_geometry()
    report = geometry_report(parse_geometry(data, observation))
    assert report["candidate_geometry_and_route_match"]
    assert not report["input_empty"] and not report["submission_enabled"] and not report["initial_empty_permit_granted"]
    assert report["button_size"] == [100, 40] and report["client_size"] == [640, 480]
    serialized = json.dumps(report)
    for private in (observation.account_id, observation.input_token, data["button_token"], ACTION.text):
        assert private not in serialized
    for field in ("bounds", "client_point", "hwnd", "receiver_pid", "receiver_thread", "hit_pid"):
        assert f'"{field}":' not in serialized


@pytest.mark.parametrize("change", [
    {"extra": "private"}, {"input_token": "c" * 64}, {"button_token": "malformed"}, {"hit_button_token": []},
    {"dpi": True}, {"dpi": 0}, {"visible": 1}, {"dpi_context_restored": False}, {"coordinate_space": "logical"},
    {"receiver_hwnd": True}, {"receiver_hwnd": 2**63}, {"hit_pid": -1}, {"receiver_thread": 0},
    {"receiver_pid": 123456789}, {"client_bounds": [0.0, 0, 640, 480]},
    {"button_bounds": [0, 0, float("nan"), 20]}, {"client_point": [2**31, 1]}, {"client_point": [1]},
])
def test_malformed_or_unbound_observations_are_rejected(change):
    observation, data = raw_geometry()
    data.update(change)
    with pytest.raises(SocialError):
        parse_geometry(data, observation)


def test_occlusion_focus_capture_and_mapping_failures_are_reported_without_effects():
    observation, data = raw_geometry()
    data.update(foreground_hwnd=0, hit_root_hwnd=0, hit_pid=0, hit_button_token="", capture_hwnd=123,
                focus_kind="other", input_busy=True, client_point=[549, 420])
    report = geometry_report(parse_geometry(data, observation))
    assert not report["candidate_geometry_and_route_match"] and not report["submission_enabled"]
    for key in ("foreground_matches", "hit_root_matches", "hit_process_matches", "hit_button_matches",
                "no_capture", "input_focus_owned", "no_hardware_interference", "client_mapping_consistent"):
        assert report["checks"][key] is False


def test_physical_negative_origin_and_fractional_bounds_are_not_rescaled():
    observation, data = raw_geometry()
    data.update(button_bounds=[-1000.5, 600.25, -900.25, 640.5], client_bounds=[-1500, 100, -700, 700], client_point=[549, 520])
    report = geometry_report(parse_geometry(data, observation))
    assert report["candidate_geometry_and_route_match"]
    data.update(client_point=[823, 780])  # A guessed 150% conversion must fail.
    assert not geometry_report(parse_geometry(data, observation))["checks"]["client_mapping_consistent"]


def test_native_before_and_after_identity_checks_preserve_draft_and_do_not_grant_input(monkeypatch):
    backend, ui, checks = setup(monkeypatch)
    ui.text = "SYNTHETIC-PRIVATE-DRAFT"
    _, data = raw_geometry()
    ui.click_geometry = lambda token: dict(data, input_token=token)
    report = backend.geometry_diagnostics()
    assert report["native_identity_verified"] and report["input_preserved"] and len(checks) == 2
    assert not ui.calls and ui.text == "SYNTHETIC-PRIVATE-DRAFT"
    assert backend.initial is None and not backend.focus_used and not backend.input_used and not backend.submit_used
    assert report["draft_sha256"] == hashlib.sha256(ui.text.encode()).hexdigest()

    def changed(token):
        ui.text += "changed"
        return dict(data, input_token=token)

    ui.click_geometry = changed
    with pytest.raises(SocialError, match="surface_changed_during_geometry_diagnostic"):
        backend.geometry_diagnostics()
    assert not ui.calls


def test_diagnostic_worker_rpc_never_creates_an_effect_permit(monkeypatch):
    backend, ui, _ = setup(monkeypatch)
    ui.text = "SYNTHETIC-PRIVATE-DRAFT"
    _, data = raw_geometry()
    ui.click_geometry = lambda token: dict(data, input_token=token)
    session = WorkerSession(backend)
    request = dict(version=1, operation_id="readonly", sequence=1, command="init", binding=asdict(BINDING), action=asdict(ACTION))
    # The backend is configured by init in the real worker.
    backend.binding = backend.action = None
    session.handle(request)
    request = dict(version=1, operation_id="readonly", sequence=2, command="geometry_diagnostics")
    response = session.handle(request)
    assert not response["result"]["geometry_diagnostics"]["submission_enabled"]
    assert session.initial is None and not session.focus_used and not session.input_used
    with pytest.raises(SocialError, match="worker_permit_order_denied"):
        session.handle(dict(request, sequence=3, command="input_once"))
    assert not ui.calls


@pytest.mark.parametrize("reaped,restored", [(False, False), (True, False), (True, True)])
def test_probe_cannot_report_success_when_reaping_or_restoration_is_unverified(monkeypatch, reaped, restored):
    from scripts import probe_wechat_post_submit as module

    class Memory:
        def __init__(self, *args, **kwargs):
            pass

    class Lease:
        def __init__(self, *args, **kwargs):
            pass

        def open(self):
            pass

        def close(self, *, restore):
            assert restore == reaped
            return restored

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self, *args):
            pass

        async def _rpc(self, command):
            assert command == "geometry_diagnostics"
            return {command: {"submission_enabled": False}}

        async def stop(self):
            return reaped

    monkeypatch.setattr(module, "DLL", SimpleNamespace(read_bytes=lambda: b"synthetic-image"))
    monkeypatch.setattr(module, "NativeMemory", Memory)
    monkeypatch.setattr(module, "AccessibilityLease", Lease)
    monkeypatch.setattr(module, "JsonSendWorker", Worker)
    monkeypatch.setattr(module, "read_identity", lambda *args: (BINDING.account.account_id, None, None))
    fingerprint = hashlib.sha256(BINDING.account.account_id.encode()).hexdigest()
    result = asyncio.run(module.ui_diagnostic(BINDING.account.window, fingerprint, lambda *args: None, kind="geometry"))
    assert result["status"] == ("geometry_observed" if reaped and restored else "not_verified")
    if not reaped or not restored:
        assert result["reason"] == "diagnostic_cleanup_unverified"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell helper")
def test_powershell_geometry_helper_compiles_and_restores_thread_dpi_context():
    engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(engine), "-NoProfile", "-NonInteractive", "-File",
                             str(Path(__file__).with_name("wechat_geometry_fixture.ps1"))], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    data = json.loads(result.stdout)
    assert data == dict(helper_compiled=True, context_changed=True, context_restored=True, independent_point_copies=True)
