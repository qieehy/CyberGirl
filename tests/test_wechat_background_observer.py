import ctypes as C
import os
import time
from types import SimpleNamespace

import pytest

from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.social.models import SocialError


def synthetic_observer():
    observer = BackgroundObserver(123)
    observer.thread = SimpleNamespace(is_alive=lambda: True)
    observer.ready.set()
    observer.samples = 1
    observer.last_sample_at = time.perf_counter()
    return observer


def test_sampling_stall_is_terminal_even_if_thread_is_alive():
    observer = synthetic_observer()
    observer.last_sample_at -= 1
    with pytest.raises(SocialError, match="sampling_stalled"):
        observer.check()


def test_sampling_gap_remains_a_failure_after_sampling_resumes():
    observer = synthetic_observer()
    observer.max_gap = 0.5
    with pytest.raises(SocialError, match="sampling_stalled"):
        observer.check()
    observer.last_sample_at = time.perf_counter()
    with pytest.raises(SocialError, match="sampling_stalled"):
        observer.check()


def test_target_foreground_event_is_latched_even_after_sample_changes():
    observer = synthetic_observer()
    observer.target_seen = True
    with pytest.raises(SocialError, match="became_foreground"):
        observer.check()


def test_hook_failure_and_stopped_stream_cannot_pass_guard():
    observer = synthetic_observer()
    observer.failure = "background_observer_hook_failed"
    with pytest.raises(SocialError, match="hook_failed"):
        observer.check()
    observer = synthetic_observer()
    observer.stop_event.set()
    with pytest.raises(SocialError, match="unavailable"):
        observer.check()


def test_immediate_current_foreground_probe_is_checked_before_later_hook_delivery():
    observer = synthetic_observer()
    observer.set_phase("value_input")

    def probe():
        observer.target_seen = True
        observer.target_foreground_phases.append(observer.phase)

    observer.current_probe = probe
    with pytest.raises(SocialError, match="became_foreground"):
        observer.check()
    assert observer.report()["target_foreground_phases"] == ["value_input"]


@pytest.mark.parametrize("object_id,child_id", [(0, 0), (-4, -2147483647)])
def test_event_identity_is_recorded_without_weakening_transient_foreground_stop(object_id, child_id):
    observer = synthetic_observer()
    observer.set_phase("value_input")
    observer._record_target_event(object_id, child_id, current_target=False, same_window=False)
    with pytest.raises(SocialError, match="became_foreground"):
        observer.check()
    report = observer.report()
    assert report["target_detection_sources"] == ["event"]
    assert report["target_foreground_sampled"] is False
    assert report["target_foreground_events"] == [dict(
        phase="value_input", object_id=object_id, child_id=child_id,
        window_self_event=object_id == child_id == 0,
        current_foreground_is_target=False, event_window_is_current=False)]


def test_current_foreground_is_distinguished_from_unconfirmed_event_and_diagnostics_are_bounded():
    observer = synthetic_observer()
    for _ in range(20):
        observer._record_target_event(0, 0, current_target=False, same_window=False)
    observer._record_target("guard_current")
    report = observer.report()
    assert report["target_detection_sources"] == ["event", "guard_current"]
    assert report["target_foreground_sampled"] is True
    assert len(report["target_foreground_events"]) == 16
    assert report["target_foreground_events_dropped"] == 4
    # Callers cannot mutate the observer's retained evidence through a report.
    report["target_foreground_events"][0]["object_id"] = 100
    assert observer.report()["target_foreground_events"][0]["object_id"] == 0


@pytest.mark.skipif(os.name != "nt", reason="read-only Windows foreground event hook")
def test_native_hook_is_released_and_report_excludes_other_app_content():
    observer = BackgroundObserver(os.getpid())
    try:
        observer.start()
        assert observer.check()
        with pytest.raises(SocialError, match="already_used"):
            observer.start()
    finally:
        assert observer.stop()
    report = observer.report()
    assert report["observer_stopped"] and report["hook_released"]
    assert report["pointer_samples"] >= 2  # Includes the final post-stop drain/sample.
    assert report["pointer_change_attribution"] == "not_determined"
    assert not ({"title", "text", "keys", "screenshots", "cursor_coordinates"} & set(report))


@pytest.mark.skipif(os.name != "nt", reason="uses only a test-owned hidden Windows control")
@pytest.mark.parametrize("object_id,child_id", [(0, 0), (-4, 123)])
def test_native_event_evidence_keeps_object_identity_and_disagreement_with_actual_foreground(object_id, child_id):
    user = C.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = ([C.c_uint32, C.c_wchar_p, C.c_wchar_p, C.c_uint32]
                                    + [C.c_int] * 4 + [C.c_void_p] * 4)
    user.CreateWindowExW.restype = C.c_void_p
    user.DestroyWindow.argtypes, user.DestroyWindow.restype = [C.c_void_p], C.c_int
    user.NotifyWinEvent.argtypes = [C.c_uint32, C.c_void_p, C.c_int32, C.c_int32]
    user.NotifyWinEvent.restype = None
    # Never show, focus or activate this fixture. It is unrelated to WeChat.
    hwnd = user.CreateWindowExW(0, "STATIC", "", 0x80000000,
                                0, 0, 32, 32, None, None, None, None)
    assert hwnd
    observer = BackgroundObserver(os.getpid())
    try:
        observer.start()
        user.NotifyWinEvent(3, hwnd, object_id, child_id)
        deadline = time.perf_counter() + 2
        while not observer.target_events and time.perf_counter() < deadline:
            time.sleep(0.01)
        with pytest.raises(SocialError, match="became_foreground"):
            observer.check()
    finally:
        assert observer.stop()
        assert user.DestroyWindow(hwnd)
    report = observer.report()
    assert report["target_detection_sources"] == ["event"]
    assert report["target_foreground_sampled"] is False
    assert report["target_foreground_events"] == [dict(
        phase="prepare", object_id=object_id, child_id=child_id,
        window_self_event=object_id == child_id == 0,
        current_foreground_is_target=False, event_window_is_current=False)]
