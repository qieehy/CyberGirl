"""Known NULL foreground in a read-only loop never relaxes writer/event guards."""
import ctypes as C

from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.receive_observer import ReceiveObserver


def resolver(hwnd, pointer):
    C.cast(pointer, C.POINTER(C.c_uint32))[0] = 123 if hwnd == 5 else 456
    return 7


def test_sender_still_rejects_null_current_window():
    observer = BackgroundObserver(123)
    assert observer._classify_owner(None, resolver, source="sample") is None
    assert observer.failure == "background_observer_foreground_unknown"
    assert "no_foreground_window_queries" not in observer.report()


def test_receive_accounts_brief_known_null_without_ignoring_later_target():
    tick = [1.0]
    observer = ReceiveObserver(123, clock=lambda: tick[0])
    assert observer._classify_owner(None, resolver, source="sample") is False
    tick[0] += 0.02
    assert observer._classify_owner(9, resolver, source="sample") is False
    assert observer.failure is None
    assert observer._classify_owner(5, resolver, source="event") is True
    observer._record_target("event")
    report = observer.report()
    assert report["foreground_target_seen"]
    assert report["no_foreground_window_queries"] == 1
    assert report["maximum_no_foreground_ms"] == 20


def test_null_event_and_nonzero_unknown_owner_remain_fatal_for_receive():
    for hwnd, source in [(None, "event"), (99, "sample"), (99, "event")]:
        observer = ReceiveObserver(123)
        assert observer._classify_owner(hwnd, lambda *_: 0, source=source) is None
        assert observer.failure == "background_observer_foreground_unknown"


def test_sustained_no_foreground_or_lock_transition_cannot_pass_receive_acceptance():
    tick = [1.0]
    observer = ReceiveObserver(123, clock=lambda: tick[0])
    observer._classify_owner(None, resolver, source="sample")
    tick[0] += 0.3
    observer._classify_owner(None, resolver, source="sample")
    observer._classify_owner(9, resolver, source="sample")
    report = observer.report()
    assert report["failure"] == "receive_observer_no_foreground_stalled"
    assert report["maximum_no_foreground_ms"] == 300
