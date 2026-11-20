"""Unsupported installations stop before opening a reader or a native process."""
import json
from types import SimpleNamespace

from scripts import probe_wechat_background_service as service
from scripts import probe_wechat_standalone as standalone


def test_standalone_reports_missing_supported_version_before_preflight(tmp_path, monkeypatch):
    (tmp_path / "4.2.0.13").mkdir()
    expected = tmp_path / "4.2.0.9" / "Weixin.dll"
    account = tmp_path / "account.json"
    account.write_text(json.dumps(dict(status="header_key_verified", header_hmac_verified=True,
        active_account_matched=True, account_fingerprint="a" * 64)), encoding="utf-8")
    output = tmp_path / "evidence.json"
    def denied():
        raise AssertionError("unsupported version must not access the process")
    monkeypatch.setattr(standalone, "DLL", expected)
    monkeypatch.setattr(standalone, "NativeMemory", lambda *_a, **_kw: SimpleNamespace(
        handle=None, preflight=denied, close=lambda: None))
    monkeypatch.setattr("sys.argv", ["probe", "--pid", "123", "--hwnd", "456",
        "--match-account-evidence", str(account), "--output", str(output), "--covered-mode", "--worker-readiness"])
    assert standalone.main() == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["reason"] == "standalone_supported_version_missing"
    assert evidence["installed_version_directories"] == ["4.2.0.13"]
    assert evidence["input_posts"] == evidence["submit_posts"] == evidence["ledger_writes"] == 0


def test_service_cli_rejects_missing_profile_before_reader_or_ledger(tmp_path, monkeypatch):
    async def denied(*_args):
        raise AssertionError("unsupported version must not start service")
    monkeypatch.setattr(service, "DLL", tmp_path / "4.2.0.9" / "Weixin.dll")
    monkeypatch.setattr(service, "run", denied)
    output = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.argv", ["probe", "--pid", "123", "--hwnd", "456", "--created-filetime", "999",
        "--match-account-evidence", str(tmp_path / "unused.json"), "--output", str(output)])
    assert service.main() == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence == dict(status="stopped", supported_version="4.2.0.9", reason="background_supported_version_missing")
