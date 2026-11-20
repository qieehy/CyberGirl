"""Selected private sending tolerates foreground changes; delivery proof still required."""

import copy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wechat_background_completion import clean_result
from tests.test_wechat_background_observer import synthetic_observer
from wechat_agent.bridges.wechat.background_completion import clean_background_result
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize(
    "foreground,focused,expected", [(777, True, True), (888, True, False), (888, False, True)]
)
def test_real_foreground_focus_is_allowed_but_background_pseudo_focus_must_release(
    monkeypatch, foreground, focused, expected
):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from scripts.wechat_private_send_worker import PrivateCapabilities

    caps = object.__new__(PrivateCapabilities)
    caps.allow_foreground = True
    caps.window = SimpleNamespace(hwnd=777)
    caps.user = SimpleNamespace(GetForegroundWindow=lambda: foreground)
    caps.last_snapshot = {"qt_has_keyboard_focus": focused}
    guards = []
    caps.guard = lambda: guards.append(True) or True
    caps.observe = lambda: None
    assert caps.verify_cleanup() is expected
    assert guards


def test_foreground_changes_allowed_only_with_explicit_policy():
    observer = synthetic_observer()
    observer._record_target("sample")
    with pytest.raises(SocialError, match="became_foreground"):
        observer.check()
    observer.allow_target_foreground = True
    assert observer.check()
    observer.current_probe = lambda: observer._record_target("guard_current")
    assert observer.check()
    assert observer.report()["foreground_target_seen"] is True
    observer.last_sample_at = time.perf_counter() - 1
    with pytest.raises(SocialError, match="sampling_stalled"):
        observer.check()


@pytest.mark.parametrize("damage", [None, "cleanup", "posts", "failure", "delivery", "event"])
def test_private_completion_accepts_foreground_but_keeps_effect_and_cleanup_checks(damage):
    text = "Cybergirl回复"
    proof = copy.deepcopy(clean_result(text))
    obs = BackgroundObserver(123, allow_target_foreground=True)
    obs._record_target("sample")
    obs._record_target("event_current")
    obs._record_target_event(0, 0, current_target=True, same_window=True)
    for key in (
        "foreground_target_seen",
        "target_foreground_sampled",
        "target_detection_sources",
        "target_foreground_phases",
        "target_foreground_events",
    ):
        proof["observation"][key] = obs.report()[key]
    assert not clean_background_result(proof, text)
    if damage == "cleanup":
        proof["worker_local_cleanup_verified"] = False
    elif damage == "posts":
        proof["control_result"]["posts_attempted"] += 1
    elif damage == "failure":
        proof["observation"]["failure"] = "native_target_changed"
    elif damage == "delivery":
        proof["control_result"]["delivery_verified"] = True
    elif damage == "event":
        proof["observation"]["target_foreground_events"][0]["text"] = "unexpected"
    assert clean_background_result(proof, text, allow_foreground=True) is (damage is None)
