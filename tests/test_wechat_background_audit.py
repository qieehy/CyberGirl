import ast
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_wechat_background.py"
SPEC = importlib.util.spec_from_file_location("background_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_explicit_utf16_still_clears_and_retries():
    result = audit.experiment("mismatch")
    assert result["events"].count("clear") == 1
    assert [e for e in result["events"] if e.startswith("input:")] == ["input:utf16", "input:gbk"]
    assert "submit" not in result["events"]


def test_no_chat_argument_can_submit_to_changed_target():
    result = audit.experiment("switch_during_input")
    assert "submit" in result["events"]
    assert not result["final_target_matches"]


@pytest.mark.parametrize("scenario", ["draft", "unreadable"])
def test_existing_or_unreadable_draft_blocks_input(scenario):
    result = audit.experiment(scenario)
    assert not result["upstream_ok"]
    assert not result["events"]


@pytest.mark.parametrize("scenario", ["input_exception", "click_exception"])
def test_exception_does_not_reenter_send(scenario):
    result = audit.experiment(scenario)
    assert result["reason"] == "input_dirty"
    assert result["events"].count("input:utf16") == 1
    assert result["events"].count("submit") <= 1
    assert "clear" not in result["events"]


def test_unreadable_after_click_is_only_upstream_success():
    result = audit.experiment("verify_unreadable")
    assert result["upstream_ok"]
    assert result["reason"] == "sent_unverified"
    assert not result["delivery_evidence"]


def test_audit_never_admits_original():
    result = audit.audit()
    assert result["source_verified"]
    assert not result["live_admitted"]
    assert all(not e["real_effects"] for e in result["experiments"])


def test_snapshot_tamper_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(
        '{"files":[{"path":"bad","sha256":"wrong"}]}', encoding="utf-8"
    )
    (tmp_path / "bad.source.txt").write_text("tampered", encoding="utf-8")
    monkeypatch.setattr(audit, "SNAPSHOT", tmp_path)
    with pytest.raises(ValueError, match="snapshot_digest_mismatch"):
        audit.verified_tree()


def test_upstream_mutex_failure_is_fail_open():
    tree = audit.verified_tree()
    guard = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_WriteGuard")
    events = []

    class Lock:
        def acquire(self):
            events.append("local_acquire")

        def release(self):
            events.append("local_release")

    namespace = {
        "_SEND_LOCK": Lock(), "_acquire_named_mutex": lambda: None,
        "_release_named_mutex": lambda handle: events.append("release:" + str(handle)),
    }
    exec(compile(ast.Module(body=[guard], type_ignores=[]), "<offline-lock>", "exec"), namespace)
    with namespace["_WriteGuard"]():
        events.append("write_permitted")
    assert events == ["local_acquire", "write_permitted", "release:None", "local_release"]
