"""Strict, lossy projection of read-only geometry; never grants an input permit."""

import re

from ...social.models import SocialError
from .directed_click import ClickSnapshot, Point, Rect

_FIELDS = {
    "input_token", "button_token", "button_bounds", "client_bounds", "client_point", "dpi", "coordinate_space",
    "receiver_hwnd", "receiver_pid", "receiver_thread", "foreground_hwnd", "hit_root_hwnd", "hit_pid",
    "hit_button_token", "capture_hwnd", "focus_kind", "enabled", "visible", "input_busy", "dpi_context_restored",
}


def parse_geometry(data, observation):
    """Transient raw snapshot, including opaque IDs. Do not serialize this value."""
    if type(data) is not dict or set(data) != _FIELDS:
        raise SocialError("geometry_shape_invalid")
    for key in ("input_token", "button_token"):
        if type(data[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", data[key]):
            raise SocialError("geometry_shape_invalid")
    if data["input_token"] != observation.input_token:
        raise SocialError("geometry_input_changed")
    if (type(data["hit_button_token"]) is not str
            or (data["hit_button_token"] and not re.fullmatch(r"[0-9a-f]{64}", data["hit_button_token"]))
            or any(type(data[k]) is not bool for k in ("enabled", "visible", "input_busy", "dpi_context_restored"))
            or data["dpi_context_restored"] is not True
            or type(data["dpi"]) is not int or not 48 <= data["dpi"] <= 768
            or data["coordinate_space"] != "physical_per_monitor_v2"
            or data["focus_kind"] not in ("chat_input", "send_button", "other")):
        raise SocialError("geometry_shape_invalid")
    for key in ("receiver_hwnd", "foreground_hwnd", "hit_root_hwnd", "capture_hwnd"):
        if type(data[key]) is not int or not 0 <= data[key] < 2**63:
            raise SocialError("geometry_shape_invalid")
    for key in ("receiver_pid", "receiver_thread", "hit_pid"):
        if type(data[key]) is not int or not 0 <= data[key] < 2**32:
            raise SocialError("geometry_shape_invalid")
    if (data["receiver_hwnd"] != observation.window.hwnd or data["receiver_pid"] != observation.window.pid
            or data["receiver_thread"] == 0):
        raise SocialError("geometry_receiver_changed")
    if (type(data["button_bounds"]) is not list or len(data["button_bounds"]) != 4
            or type(data["client_bounds"]) is not list or len(data["client_bounds"]) != 4
            or any(type(v) is not int for v in data["client_bounds"])
            or type(data["client_point"]) is not list or len(data["client_point"]) != 2):
        raise SocialError("geometry_shape_invalid")
    values = {key: value for key, value in data.items() if key not in ("input_token", "dpi_context_restored")}
    values.update(button_bounds=Rect(*data["button_bounds"]), client_bounds=Rect(*data["client_bounds"]),
                  client_point=Point(*data["client_point"]))
    return ClickSnapshot(input=observation, **values)


def geometry_report(snapshot):
    """Geometry/route observations only, independent of draft authorization.

    In particular, do not synthesize an empty initial observation to run the
    candidate's effect verifier against an existing user draft.
    """
    button, client = snapshot.button_bounds, snapshot.client_bounds
    point, center = snapshot.client_point, button.center
    window = snapshot.input.window
    checks = dict(
        button_size_bounded=8 <= button.right - button.left <= 512 and 8 <= button.bottom - button.top <= 256,
        button_inside_client=(client.left <= button.left < button.right <= client.right
                              and client.top <= button.top < button.bottom <= client.bottom),
        center_has_margin=button.contains(center, margin=2),
        client_mapping_consistent=point.x == center.x - client.left and point.y == center.y - client.top,
        client_point_packable=0 <= point.x <= 32767 and 0 <= point.y <= 32767,
        foreground_matches=snapshot.foreground_hwnd == window.hwnd,
        hit_root_matches=snapshot.hit_root_hwnd == window.hwnd,
        hit_process_matches=snapshot.hit_pid == window.pid,
        hit_button_matches=snapshot.hit_button_token == snapshot.button_token,
        no_capture=snapshot.capture_hwnd == 0,
        input_focus_owned=snapshot.input.focused and snapshot.focus_kind == "chat_input",
        no_hardware_interference=not snapshot.input_busy,
        button_enabled=snapshot.enabled,
        button_visible=snapshot.visible,
    )
    return dict(read_only=True, submission_enabled=False, initial_empty_permit_granted=False,
                dpi=snapshot.dpi, coordinate_space=snapshot.coordinate_space, dpi_context_restored=True,
                button_size=[button.right - button.left, button.bottom - button.top],
                client_size=[client.right - client.left, client.bottom - client.top],
                checks=checks, candidate_geometry_and_route_match=all(checks.values()),
                input_empty=snapshot.input.empty, draft_sha256=snapshot.input.draft_sha256)
