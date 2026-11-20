"""Static route qualification against the installed pinned DLL; no live IO."""

import copy
import json

import pytest

from scripts.inspect_wechat_keyboard_route import (
    INPUT_META,
    INPUT_VTABLE,
    _trace,
    inspect_keyboard_route,
)
from scripts.verify_wechat_accessibility import DLL
from scripts.wechat_pinned_image import PinnedImage


def historical():
    return dict(status="observed", collector_reaped=True, restore_verified=True, pid=123456789,
                observation=dict(native_object=dict(stable=True, canonical_conversation_verified=True,
                    matched_database_account=True, private_unused="SYNTHETIC-DO-NOT-EXPORT",
                    parent_chains={"chat_input": {"entries": [dict(class_name="mmui::ChatInputField",
                        meta_object_rva=INPUT_META, qobject_vtable_rva=INPUT_VTABLE)]}})))


@pytest.fixture(scope="module")
def image():
    if not DLL.exists():
        pytest.skip("local pinned Weixin binary not installed")
    return PinnedImage(DLL.read_bytes())


def test_static_keyboard_qualification_never_grants_submission_or_copies_historical_private_data(image):
    result = inspect_keyboard_route(image.image, historical())
    assert not result["submission_enabled"] and not result["live_process_accessed"]
    assert not any(row["qualified"] for row in result["candidates"])
    assert result["keydown_has_key_record_store_path"] and result["keyup_uses_prior_key_record"]
    assert result["wm_char_qt_events"] == [dict(type=6, key=0), dict(type=7, key=0)]
    assert result["return_send_argument_values"] == [1, 2] and not result["current_send_preference_observed"]
    assert "SYNTHETIC-DO-NOT-EXPORT" not in json.dumps(result) and "123456789" not in json.dumps(result)


@pytest.mark.parametrize("changed", [INPUT_VTABLE + 5 * 8, 0x21B2023, 0x94F95BC + 4, 0x21B0D00,
                                     0x1BF1416, 0x26E6C09, 0x26E78E3, 0x26E7B2D])
def test_changed_control_flow_or_event_state_cannot_reuse_the_static_conclusion(image, monkeypatch, changed):
    original = image.at

    def altered(rva, size, **kwargs):
        data = original(rva, size, **kwargs)
        if rva <= changed < rva + size:
            position = changed - rva
            return data[:position] + bytes([data[position] ^ 1]) + data[position + 1:]
        return data

    # Hash checking is exercised by the real constructor. Alter selected reads
    # here to independently verify that each semantic path is actually checked.
    monkeypatch.setattr(image, "at", altered)
    with pytest.raises(ValueError):
        _trace(image)


@pytest.mark.parametrize("field", ["restore_verified", "stable", "qobject_vtable_rva"])
def test_unverified_historical_binding_is_rejected_before_reading_a_binary(field):
    data = copy.deepcopy(historical())
    if field == "restore_verified":
        data[field] = False
    elif field == "stable":
        data["observation"]["native_object"][field] = False
    else:
        data["observation"]["native_object"]["parent_chains"]["chat_input"]["entries"][0][field] = 123
    with pytest.raises(ValueError, match="historical"):
        inspect_keyboard_route(b"not a client binary", data)
