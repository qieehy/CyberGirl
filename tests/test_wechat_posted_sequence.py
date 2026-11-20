"""Complete upstream gesture with synthetic IO, no application access."""

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts import probe_wechat_directed_trial as trial
from scripts.wechat_native_worker import make_backend
from tests.test_wechat_native_sender import ACTION, BINDING
from tests.test_wechat_posted_click import QueueModel
from tests.test_wechat_posted_native import NativeModel
from wechat_agent.bridges.wechat.directed_click import WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE
from wechat_agent.bridges.wechat.posted_sequence import PostedSequence
from wechat_agent.bridges.wechat.sequence_native import SEQUENCE_MARKER, NativeSequenceTrial
from wechat_agent.social.models import SocialError


def sequence(model):
    return PostedSequence(BINDING, ACTION, model.initial, observe=model.observe, post=model.post,
        cancel=model.cancel, record=model.record, clock=lambda: model.now, sleep=model.sleep)


def test_complete_sequence_commits_every_intent_and_checks_once_before_all_effects():
    model = QueueModel(press_missing=True)
    original = model.observe
    reads = []

    def observe():
        reads.append(len(model.messages()))
        return original()

    model.observe = observe
    candidate = sequence(model)
    result = candidate.perform_once()
    assert result.state == "REQUESTED" and result.reason == "posted_sequence_enqueued_not_delivery"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    assert reads == [0]
    assert model.events[:4] == ["CLICK_BEGIN_INTENT", "CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"]
    posts = [event for event in model.events if isinstance(event, tuple) and event[0] == "post"]
    assert posts[-1][2] - posts[-2][2] >= 0.07
    with pytest.raises(SocialError, match="already_used"):
        candidate.perform_once()


@pytest.mark.parametrize("stage", ["CLICK_BEGIN_INTENT", "CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT"])
def test_failed_precommit_produces_no_window_messages(stage):
    model = QueueModel(fail_stage=stage)
    result = sequence(model).perform_once()
    assert not model.messages() and not result.down_attempted and not result.cancel_attempted
    assert result.state == ("UNKNOWN" if stage in {"CLICK_DOWN_INTENT", "CLICK_UP_INTENT"} else "NOT_STARTED")


@pytest.mark.parametrize("message", [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP])
def test_failed_post_is_terminal_and_cancel_is_only_enqueued(message):
    model = QueueModel(fail_message=message)
    result = sequence(model).perform_once()
    assert result.state == "UNKNOWN"
    all_messages = [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    assert model.messages() == all_messages[:all_messages.index(message) + 1]
    assert result.cancel_enqueued == (message != WM_MOUSEMOVE)
    assert not result.cancel_acknowledged


def test_target_changed_during_last_precommit_is_rejected_before_move():
    model = QueueModel(mutate_stage="CLICK_UP_INTENT")
    model.before = replace(model.before, receiver_hwnd=BINDING.account.window.hwnd + 1)
    result = sequence(model).perform_once()
    assert result.state == "UNKNOWN" and not model.messages()


@pytest.mark.parametrize("delay", [0, 0.6])
def test_short_or_late_settle_never_emits_up(delay):
    model = QueueModel()
    model.sleep = lambda seconds: setattr(model, "now", model.now + delay)
    result = sequence(model).perform_once()
    assert result.reason == "posted_sequence_settle_expired"
    assert model.messages() == [WM_MOUSEMOVE, WM_LBUTTONDOWN] and result.cancel_enqueued


@pytest.mark.parametrize("outcome", ["complete", "uncleared", "foreign_editor", "missing_pattern"])
def test_native_sequence_still_requires_strict_completion(outcome):
    model = NativeModel(outcome)
    model.backend.__class__ = NativeSequenceTrial
    native_checks = []
    model.backend._native_guard = lambda message: native_checks.append(message)
    result = model.backend.submit_once(model.backend.initial.input_token)
    assert native_checks == [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP]
    assert result.state == ("REQUESTED" if outcome == "complete" else "UNKNOWN")
    assert not result.cancel_acknowledged


def test_native_guard_rechecks_canonical_identity_and_never_invokes_uia(monkeypatch):
    model = NativeModel("complete")
    model.backend.__class__ = NativeSequenceTrial
    model.backend.memory, model.backend.account_reader = object(), object()
    model.backend.runtime_ids = {"selected_session": [1], "chat_input": [2]}
    model.backend.ui = SimpleNamespace(observe=lambda: pytest.fail("no UIA within complete gesture"))
    calls = []

    def verify(memory, row, editor, **kwargs):
        calls.append((memory, row, editor, kwargs))
        raise SocialError("synthetic_identity_changed")

    monkeypatch.setattr("wechat_agent.bridges.wechat.sequence_native.verify_current_filehelper", verify)
    with pytest.raises(SocialError, match="identity_changed"):
        model.backend._native_guard(WM_LBUTTONDOWN)
    assert len(calls) == 1 and calls[0][1:3] == ([1], [2])


def test_factory_is_opt_in_and_new_marker_is_exact():
    model = NativeModel("complete")
    instance = make_backend(BINDING.account.window, model.backend.fingerprint, sequence_trial=True, read_only=True)
    assert type(instance) is NativeSequenceTrial and instance.memory is None
    with pytest.raises(SocialError, match="marker_denied"):
        instance.configure(BINDING, ACTION)
    fresh = make_backend(BINDING.account.window, model.backend.fingerprint, sequence_trial=True)
    fresh.configure(BINDING, replace(ACTION, text=SEQUENCE_MARKER))
    with pytest.raises(SocialError, match="route_conflict"):
        make_backend(BINDING.account.window, model.backend.fingerprint, sequence_trial=True, posted_trial=True)


@pytest.mark.parametrize("send", [False, True])
@pytest.mark.parametrize("physical", [False, True])
def test_sequence_cli_uses_exact_route_and_does_not_create_actions_in_readonly_mode(tmp_path, monkeypatch, send, physical):
    evidence, output = tmp_path / "account.json", tmp_path / "result.json"
    evidence.write_text(json.dumps(dict(status="header_key_verified", header_hmac_verified=True, account_fingerprint="f" * 64)))
    argv = ["probe", "--pid", "1", "--hwnd", "2", "--created-filetime", "3",
        "--match-account-evidence", str(evidence), "--output", str(output)]
    route = "physical" if physical else "sequence"
    argv += (["--send-"+route+"-reviewed-once", "--approval-reference", "new-specific-approval",
        "--review-unknown-action", "a" * 64, "--uncertainty-review-reference", "specific-uncertainty-review"]
        if send else ["--"+route+"-readiness"])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(trial, "Ledger", lambda *args: pytest.fail("test CLI must not open real ledger"))

    async def observe(window, fingerprint, journal, **kwargs):
        assert not send and kwargs["sequence"] == (not physical) and kwargs["physical"] == physical and kwargs["posted"]
        command = trial.command(window, fingerprint, read_only=True, sequence=not physical, physical=physical)
        assert "--"+route+"-fixed-trial" in command and "--read-only" in command
        return dict(submission_enabled=False)

    async def dispatch(window, fingerprint, journal, reference, result, **kwargs):
        assert send and kwargs["sequence"] == (not physical) and kwargs["physical"] == physical and kwargs["posted"]
        assert reference == "new-specific-approval" and kwargs["reviewed_unknowns"] == ("a" * 64,)
        result.update(status="directed_trial_unconfirmed")

    monkeypatch.setattr(trial, "readiness", observe)
    monkeypatch.setattr(trial, "bounded_send_trial", dispatch)
    assert trial.main() == (1 if send else 0)
    result = json.loads(output.read_text())
    assert result["mode"] == ("send_"+route+"_reviewed_once" if send else route+"_readiness_only")


def test_snapshot_phase_report_preserves_checks_and_contains_only_timings(monkeypatch):
    from wechat_agent.bridges.wechat import directed_native

    model = NativeModel("complete")
    backend = model.backend
    backend.runtime_ids = {"button": [1], "chat_input": [2]}
    backend.memory = object()

    def advance(seconds, result):
        model.now += seconds
        return result

    backend.inspect = lambda: advance(0.05, model.before.input)
    backend.messages = SimpleNamespace(validate_class=lambda: advance(0.01, None))
    backend.ui = SimpleNamespace(click_geometry=lambda token: advance(0.08, {}))
    monkeypatch.setattr(directed_native, "parse_geometry", lambda *args: model.before)
    monkeypatch.setattr(directed_native, "inspect_clicked_connection", lambda *args: advance(0.02,
        dict(stable=True, connections=1, same_input_view=True, pressed_bit_set=False)))
    directed_native.NativeDirectedTrial._snapshot(backend)
    assert backend.snapshot_phases_ms == dict(initial_identity=50, class_validation=10, geometry=80,
        connection_and_press=20, final_identity=50, press_read_window_start=140, press_read_window_end=160, total=210)
    assert all(type(value) in (int, float) for value in backend.snapshot_phases_ms.values())


@pytest.mark.parametrize("empty", [True, False])
def test_sequence_bound_focus_preparation_still_requires_empty_identity(empty):
    model = NativeModel("complete")
    backend = model.backend
    backend.__class__ = NativeSequenceTrial
    backend.input_used = False
    calls = []
    backend.readiness_diagnostics = lambda: dict(account_and_filehelper_verified=True,
        both_draft_patterns_empty=empty, same_editor_and_button=True, no_capture_or_hardware_input=True,
        button_not_pressed=True, class_entry_matches_pinned_code=True, foreground_hit_and_focus=bool(calls))
    backend.inspect = lambda: backend.initial
    backend.ui = SimpleNamespace(focus_once=lambda token: calls.append(token))
    if empty:
        backend.prepare_input_focus()
        assert calls == [backend.initial.input_token]
    else:
        with pytest.raises(SocialError, match="prerequisites_failed"):
            backend.prepare_input_focus()
        assert calls == []
