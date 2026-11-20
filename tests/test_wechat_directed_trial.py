"""Trial controller cleanup and truthful echo reporting; all effects are synthetic."""

import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from scripts import probe_wechat_directed_trial as trial
from tests.test_wechat_bridge import record
from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("scenario", ["reader_failure", "readiness_failure", "echo_absent", "echo_present", "echo_source_changed"])
@pytest.mark.parametrize("posted", [False, True])
def test_trial_stops_children_restores_gate_and_does_not_mislabel_missing_echo(tmp_path, monkeypatch, scenario, posted):
    path = tmp_path / "original.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
    events = []
    marker = trial.POSTED_TRIAL_MARKER if posted else trial.TRIAL_MARKER

    class Reader:
        async def start(self, *args):
            events.append("reader_start")
            if scenario == "reader_failure":
                raise SocialError("synthetic_reader_failure")
            return {}

        async def rpc(self, command, **kwargs):
            if command == "ack":
                return {}
            assert command == "page"
            if scenario == "echo_source_changed":
                raise SocialError("source_changed_during_snapshot")
            echo = record(binding=binding, sender_id=binding.account.self_id, text=marker)
            return {"records": [asdict(echo)] if scenario == "echo_present" else []}

        async def stop(self):
            events.append("reader_stop")
            return True

    class Service:
        def __init__(self, ledger, *args):
            self.ledger = ledger

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def dispatch(self, action_id):
            action = self.ledger.claim(action_id)
            assert action.text == marker
            state = SendState(self.ledger)
            op = state.begin(binding, action, {"message_0": Cursor(0, 0)})
            state.permit(op, binding, action, "INPUT_PERMITTED")
            state.permit(op, binding, action, "SUBMIT_PERMITTED")
            state.finish(op, reaped=True, restored=True, reason="synthetic_unknown")
            self.ledger.finish_delivery(action_id, "UNCONFIRMED", "synthetic_unknown")
            return self.ledger.action_summary(action_id)

    async def readiness(*args, **kwargs):
        assert kwargs["posted"] == posted
        if scenario == "readiness_failure":
            raise SocialError("synthetic_readiness_failure")
        return facts

    async def no_wait(*args):
        pass

    outer = SimpleNamespace(open=lambda: events.append("gate_open"),
        close=lambda **kwargs: events.append(("gate_close", kwargs["restore"])) or True)
    catalog = SimpleNamespace(attach=lambda *args, **kwargs: binding,
        checkpoints=lambda: {"message_0.db": {"shard": "message_0", "start": {"sort_seq": 0, "local_id": 0}, "local_fence": 0}})
    monkeypatch.setattr(trial, "LEDGER", path)
    monkeypatch.setattr(trial, "Ledger", lambda path: Ledger(path, now=lambda: NOW))
    monkeypatch.setattr(trial, "lease", lambda *args: outer)
    monkeypatch.setattr(trial, "JsonReaderWorker", lambda *args: Reader())
    monkeypatch.setattr(trial, "ReaderCatalog", lambda ledger: catalog)
    monkeypatch.setattr(trial, "readiness", readiness)
    monkeypatch.setattr(trial, "prior_processes_exited", lambda *args: True)
    monkeypatch.setattr(trial, "SocialService", Service)
    monkeypatch.setattr(trial.asyncio, "sleep", no_wait)
    output = {}
    coro = trial.send_trial(binding.account.window, "f" * 64, lambda *args: None,
                            "synthetic-operator-approval", output, posted=posted)
    if scenario.endswith("failure"):
        with pytest.raises(SocialError, match="synthetic_"):
            asyncio.run(coro)
        assert "action_id" not in output
    else:
        asyncio.run(coro)
        assert output["status"] == ("directed_trial_echo_observed" if scenario == "echo_present"
                                    else "directed_trial_unconfirmed")
        assert output["echo_status"] == ("matched" if scenario == "echo_present" else "not_unique_or_absent")
        assert output["delivery_state"] == "UNCONFIRMED" and output["phone_confirmation_required"]
        if scenario == "echo_source_changed":
            assert output["echo_error_reason"] == "source_changed_during_snapshot"
            assert output["reason"] == "synthetic_unknown"
    assert output["reader_stopped"] and output["outer_restore_verified"]
    assert events[-2:] == ["reader_stop", ("gate_close", True)]
    with Ledger(path, now=lambda: NOW) as ledger:
        assert not ledger._db.execute("SELECT 1 FROM bridge_bindings WHERE active=1").fetchone()
        assert ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == (1 if scenario.endswith("failure") else 2)
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]


@pytest.mark.parametrize("extra", ["--prepare-focus", "--wait-for-operator", "--wait-for-foreground", "--send-reviewed-once", "--send-posted-reviewed-once", "--confirm-phone"])
def test_posted_readiness_rejects_every_effectful_mode_before_reading_evidence(tmp_path, monkeypatch, extra):
    argv = ["probe", "--pid", "1", "--hwnd", "2", "--created-filetime", "3",
            "--match-account-evidence", str(tmp_path / "missing.json"), "--output", str(tmp_path / "out.json"),
            "--posted-readiness", extra]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        trial.main()
    assert exc.value.code == 2 and not (tmp_path / "out.json").exists()


def test_posted_readiness_cli_cannot_open_a_ledger_or_create_a_send_trial(tmp_path, monkeypatch):
    evidence, output = tmp_path / "account.json", tmp_path / "out.json"
    evidence.write_text(json.dumps({"status": "header_key_verified", "header_hmac_verified": True,
                                   "account_fingerprint": "f" * 64}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["probe", "--pid", "1", "--hwnd", "2", "--created-filetime", "3",
        "--match-account-evidence", str(evidence), "--output", str(output), "--posted-readiness"])

    def forbidden(*args, **kwargs):
        pytest.fail("observation-only CLI reached an effectful path")

    async def observe(window, fingerprint, journal, **kwargs):
        assert kwargs == {"prepare_focus": False, "posted": True, "sequence": False, "physical": False}
        assert fingerprint == "f" * 64
        command = trial.command(window, fingerprint, read_only=True, posted=True)
        assert "--posted-fixed-trial" in command and "--read-only" in command and "--directed-fixed-trial" not in command
        return {"transport": "PostMessageW", "submission_enabled": False}

    monkeypatch.setattr(trial, "readiness", observe)
    monkeypatch.setattr(trial, "Ledger", forbidden)
    monkeypatch.setattr(trial, "bounded_send_trial", forbidden)
    monkeypatch.setattr(trial, "confirm_phone", forbidden)
    assert trial.main() == 0
    saved = json.loads(output.read_text())
    assert saved["mode"] == "posted_readiness_only" and saved["status"] == "readiness_observed"


def test_internal_posted_readiness_rejects_focus_before_constructing_any_worker(monkeypatch):
    monkeypatch.setattr(trial, "lease", lambda *args: pytest.fail("unexpected gate"))
    with pytest.raises(SocialError, match="focus_denied"):
        asyncio.run(trial.readiness(None, "f" * 64, None, prepare_focus=True, posted=True))


def test_posted_readiness_measures_warm_snapshot_in_same_readonly_worker(monkeypatch):
    from tests.test_wechat_native_sender import BINDING

    calls = []
    account = BINDING.account.account_id
    gate = SimpleNamespace(memory=None, open=lambda: calls.append("open"), close=lambda **kwargs: calls.append("restore") or True)

    class Worker:
        async def start(self, operation, binding, action):
            assert action.text == trial.POSTED_TRIAL_MARKER
            calls.append("start")

        async def _rpc(self, command):
            assert command == "readiness_diagnostics"
            calls.append("read")
            return {"readiness_diagnostics": {"snapshot_observation_ms": 4000 if calls.count("read") == 1 else 10}}

        async def stop(self):
            calls.append("stop")
            return True

    monkeypatch.setattr(trial, "lease", lambda *args: gate)
    monkeypatch.setattr(trial, "JsonSendWorker", lambda *args: Worker())
    monkeypatch.setattr(trial, "DLL", SimpleNamespace(read_bytes=lambda: b"synthetic"))
    monkeypatch.setattr(trial, "read_identity", lambda *args: (account, None, None))
    monkeypatch.setattr(trial, "configured_roots", lambda: ())
    result = asyncio.run(trial.readiness(BINDING.account.window, hashlib.sha256(account.encode()).hexdigest(), None, posted=True))
    assert result["snapshot_observation_ms"] == 10 and result["first_snapshot_observation_ms"] == 4000
    assert calls == ["open", "start", "read", "read", "stop", "restore"]


@pytest.mark.parametrize("wait_foreground", [False, True])
def test_new_fixed_trial_cli_passes_only_explicit_uncertainty_acceptance_and_remains_unconfirmed(tmp_path, monkeypatch, wait_foreground):
    evidence, output = tmp_path / "account.json", tmp_path / "out.json"
    evidence.write_text(json.dumps({"status": "header_key_verified", "header_hmac_verified": True,
                                   "account_fingerprint": "f" * 64}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["probe", "--pid", "1", "--hwnd", "2", "--created-filetime", "3",
        "--match-account-evidence", str(evidence), "--output", str(output), "--send-posted-reviewed-once",
        "--approval-reference", "new-trial-approval", "--review-unknown-action", "a" * 64,
        "--uncertainty-review-reference", "prior-uncertainty-acceptance"])
    if wait_foreground:
        sys.argv.append("--wait-for-foreground")

    async def send(window, fingerprint, journal, approval_ref, result, **kwargs):
        assert kwargs == dict(prepare_focus=False, wait_for_operator=False, posted=True, sequence=False, physical=False,
                             reviewed_unknowns=("a" * 64,), uncertainty_approval_ref="prior-uncertainty-acceptance", wait_foreground=wait_foreground)
        assert approval_ref == "new-trial-approval"
        result.update(status="directed_trial_unconfirmed", delivery_state="UNCONFIRMED")

    monkeypatch.setattr(trial, "bounded_send_trial", send)
    assert trial.main() == 1
    saved = json.loads(output.read_text())
    assert saved["mode"] == "send_posted_reviewed_once" and saved["delivery_state"] == "UNCONFIRMED"
    assert saved["marker_sha256"] == hashlib.sha256(trial.POSTED_TRIAL_MARKER.encode()).hexdigest()


@pytest.mark.parametrize("scenario", ["becomes_ready", "draft", "timeout", "process_exit", "late_ready", "stalled_clock"])
def test_approved_foreground_wait_observes_real_readiness_without_activation_or_timeout_consent(scenario):
    from tests.test_wechat_native_sender import BINDING

    now, queries, validations = 0.0, [], []

    async def sleep(delay):
        nonlocal now
        if scenario != "stalled_clock":
            now += delay

    def foreground():
        return BINDING.account.window.hwnd if scenario in {"draft", "late_ready"} or scenario == "becomes_ready" and now >= 0.4 else 0

    def validate():
        validations.append(True)
        if scenario == "process_exit" and len(validations) > 1:
            raise SocialError("synthetic_process_exited")

    class Worker:
        async def _rpc(self, command):
            nonlocal now
            assert command == "readiness_diagnostics"
            queries.append(now)
            report = {k: True for k in trial.READY_KEYS}
            report["both_draft_patterns_empty"] = scenario != "draft"
            report["foreground_hit_and_focus"] = now >= 0.6 or scenario == "late_ready"
            if scenario == "late_ready":
                now += 2
            return {"readiness_diagnostics": report}

    memory = SimpleNamespace(u=SimpleNamespace(GetForegroundWindow=foreground), validate=validate)
    coroutine = trial.await_bound_foreground(Worker(), memory, BINDING.account.window,
        wait_seconds=1, clock=lambda: now, sleep=sleep)
    if scenario == "becomes_ready":
        report = asyncio.run(coroutine)
        assert all(report[k] is True for k in trial.READY_KEYS) and queries == [0.4, pytest.approx(0.6)]
    else:
        with pytest.raises(SocialError):
            asyncio.run(coroutine)
    if scenario in {"timeout", "process_exit", "stalled_clock"}:
        assert queries == []
    assert len(validations) <= 301 and len(queries) <= 32
