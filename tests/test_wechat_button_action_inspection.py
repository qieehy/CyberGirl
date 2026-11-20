"""Read-only action metadata, including the localized slots before doAction."""

from types import SimpleNamespace

import pytest

from wechat_agent.bridges.wechat import standalone_identity as identity
from wechat_agent.bridges.wechat.qt_connections import BUTTON_META, BUTTON_VTABLE
from wechat_agent.social.models import SocialError


@pytest.mark.parametrize("damage", [None, "cast", "live_code", "class", "unstable"])
def test_readonly_button_inspection_checks_cast_and_reads_doaction_slot_four(monkeypatch, damage):
    base, interface, button, table, action_table = 1000, 2000, 3000, 1500, 1800
    addresses = {interface: table, table + 19 * 8: 4000, interface + 16: action_table,
                 action_table + 1 * 8: 5000, action_table + 4 * 8: 6000}
    prefix = bytes.fromhex("4883c11031c083fa03480f44c1c3")
    code = {4000: (b"\x90" * 14 if damage == "cast" else prefix) + b"\xCC" * 370,
            5000: b"\x01" * 384, 6000: b"\x04" * 384}
    seen = []

    class Reads:
        def __init__(self, _memory):
            pass

        def pointer(self, address):
            seen.append(address)
            return addresses[address]

        def read(self, address, size):
            assert size == 384
            return b"\x05" * size if damage == "live_code" and address == 6000 else code[address]

        def stable(self):
            if damage == "unstable":
                raise SocialError("native_object_changed")

    def at(rva, size, *, executable):
        assert size == 384 and executable
        return code[rva + base]

    monkeypatch.setattr(identity, "Reads", Reads)
    monkeypatch.setattr(identity, "accessible_interface", lambda *_: interface)
    monkeypatch.setattr(identity, "object_from_interface", lambda *_: (button, 0))
    monkeypatch.setattr(identity, "static_meta", lambda *_: dict(
        class_name="other" if damage == "class" else "mmui::XOutlineButton",
        meta_object_rva=BUTTON_META, qobject_vtable_rva=BUTTON_VTABLE))

    def run():
        return identity.inspect_button_action_route(SimpleNamespace(base=base), [3, 0x80000001], SimpleNamespace(at=at))

    if damage:
        with pytest.raises(SocialError):
            run()
    else:
        result = run()
        assert result["action_methods"][1]["slot"] == 4
        assert result["action_methods"][1]["function_rva"] == 5000
        assert action_table + 4 * 8 in seen and action_table + 2 * 8 not in seen
