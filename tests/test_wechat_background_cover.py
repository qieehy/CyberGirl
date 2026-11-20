"""Covered-window scenario checks touch no real desktop windows."""

import ctypes as C
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.wechat_background_control import StandaloneCoveredControl
from scripts.wechat_standalone_backend import StandaloneCapabilities
from tests.test_wechat_background_control import ACTION, BINDING, Rig
from tests.test_wechat_background_native import Function
from tests.test_wechat_channel_review import NOW, prior
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_contract import COVERED_MARKER, COVERED_REQUEST
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("failure", [None, "other_covering_app", "foreground_changed", "wechat_front", "wechat_owner",
                                     "one_uncovered_point", "one_wechat_point", "foreground_became_wechat", "dpi"])
def test_occlusion_requires_all_five_points_under_other_apps_and_restores_dpi(failure):
    window = BINDING.account.window
    foreground = window.hwnd + 100
    points, dpi_calls, foreground_calls = [], [], []

    def fg():
        foreground_calls.append(1)
        if failure == "wechat_front":
            return window.hwnd
        if failure == "foreground_became_wechat" and len(foreground_calls) > 1:
            return window.hwnd
        return foreground + int(failure == "foreground_changed" and len(foreground_calls) > 1)

    def owner(_hwnd, pointer):
        is_target = failure == "wechat_owner" or _hwnd == window.hwnd
        C.cast(pointer, C.POINTER(C.c_uint32)).contents.value = window.pid if is_target else window.pid + 1
        return 11

    def point(value):
        points.append((value.x, value.y))
        if failure == "one_uncovered_point" and len(points) == 3:
            return 0
        return 56 if failure == "one_wechat_point" and len(points) == 3 else 55

    def dpi(value):
        dpi_calls.append(value)
        return 0 if failure == "dpi" else 777

    capability = StandaloneCapabilities.__new__(StandaloneCapabilities)
    capability.window, capability.require_cover = window, True
    capability.cover_points = ((1, 1), (10, 1), (1, 10), (10, 10), (5, 5))
    def root(hit, _flags):
        if hit == 56:
            return window.hwnd
        return foreground + int(failure == "other_covering_app")

    capability.user = SimpleNamespace(GetForegroundWindow=Function(fg), GetWindowThreadProcessId=Function(owner),
        WindowFromPoint=Function(point), GetAncestor=Function(root), SetThreadDpiAwarenessContext=Function(dpi))
    if failure not in {None, "other_covering_app", "foreground_changed"}:
        with pytest.raises(SocialError):
            capability._cover()
    else:
        capability._cover()
        assert points == list(capability.cover_points)
    if failure not in {"wechat_front", "wechat_owner", "dpi"}:
        assert dpi_calls[-1] == 777


def test_covered_route_keeps_one_return_and_no_mouse():
    rig = Rig()
    post = rig.post

    def routed(message, wparam, lparam):
        accepted = post(message, wparam, lparam)
        if message == 0x100:
            assert rig.body == COVERED_MARKER
            rig.body = ""
        return accepted

    rig.post = routed
    control = StandaloneCoveredControl(BINDING, replace(ACTION, text=COVERED_MARKER), observe=rig.observe,
        guard=rig.guard, transport=rig, record=rig.record, clock=lambda: rig.now, sleep=rig.sleep)
    result = control.run_once(write_guard=nullcontext())
    assert result.state == "REQUESTED" and not result.delivery_verified
    assert [p[0] for p in rig.posts][-3:] == [0x100, 0x101, 8]
    assert not any(p[0] in {0x200, 0x201, 0x202} for p in rig.posts)


def test_covered_review_requires_fresh_occlusion_fact_not_just_a_hidden_or_visible_window(tmp_path):
    with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
        binding, facts = prior(ledger)
        facts.pop("foreground_hit_and_focus")
        facts.pop("no_capture_or_hardware_input")
        facts.update(standalone_identity_verified=True, background_observer_active=True, no_capture=True)
        with pytest.raises(SocialError, match="live_facts_incomplete"):
            channel_review.authorize(ledger, binding, COVERED_REQUEST, COVERED_MARKER, facts,
                approval_ref="synthetic-covered-approval", backend_kind="standalone_background_covered")
        facts["covered_by_other_app"] = True
        assert channel_review.authorize(ledger, binding, COVERED_REQUEST, COVERED_MARKER, facts,
            approval_ref="synthetic-covered-approval", backend_kind="standalone_background_covered")
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
