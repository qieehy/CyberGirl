"""Keyless native account proof with synthetic memory; no native process access."""

import struct

import pytest

from scripts import probe_wechat_account as probe
from wechat_agent.bridges.wechat.keyprobe import config_identity
from wechat_agent.social.models import SocialError


def test_keyless_identity_never_reads_key_fields_and_rejects_ambiguous_layout():
    base, first, config = 0x10000, 0x20000, 0x30000
    data = b"global_config\0\0\0" + struct.pack("<QQ", 13, 15)
    memory = {
        (base + 16 - 0x138, 8): struct.pack("<Q", first),
        (first + 0x68, 8): struct.pack("<Q", config),
        (config + 0x48, 32): b"fixture-self".ljust(16, b"\0") + struct.pack("<QQ", 12, 15),
    }
    reads = []

    def read(address, size):
        reads.append((address, size))
        return memory[(address, size)]  # No key field exists in this fixture.

    assert config_identity(read, base, data) == ("fixture-self", config)
    assert reads == list(memory)
    before = len(reads)
    with pytest.raises(SocialError, match="config_landmark_not_unique"):
        config_identity(read, base, data + data)
    assert len(reads) == before
    memory[(config + 0x48, 32)] = b"invalid/name".ljust(16, b"\0") + struct.pack("<QQ", 12, 15)
    with pytest.raises(SocialError, match="config_identity_unrecognized"):
        config_identity(read, base, data)


def test_keyless_account_directory_and_second_read_detect_account_change(tmp_path, monkeypatch):
    data = b"global_config\0\0\0" + struct.pack("<QQ", 13, 15)
    module_base, section_rva, first, config = 0x100000, 0x1000, 0x200000, 0x300000
    monkeypatch.setattr(probe, "inspect", lambda image: {"sections": [dict(name=".data", rva=section_rva, virtual_size=len(data))]})
    target = tmp_path / "fixture-self_abcd"
    (target / "db_storage").mkdir(parents=True)
    (tmp_path / "other-account/db_storage").mkdir(parents=True)

    class Memory:
        base = module_base
        changed = False

        def __init__(self):
            self.reads, self.checks, self.name_reads = [], 0, 0

        def validate(self):
            self.checks += 1

        def read_bytes(self, address, size):
            self.reads.append((address, size))
            if address == config + 0x48:
                self.name_reads += 1
                name = b"different" if self.changed and self.name_reads == 2 else b"fixture-self"
                return name.ljust(16, b"\0") + struct.pack("<QQ", len(name), 15)
            return {
                (module_base + section_rva, len(data)): data,
                (module_base + section_rva + 16 - 0x138, 8): struct.pack("<Q", first),
                (first + 0x68, 8): struct.pack("<Q", config),
            }[(address, size)]

    memory = Memory()
    assert probe.read_identity(memory, b"synthetic-image", [tmp_path]) == ("fixture-self", target.resolve(), config)
    assert memory.checks == 2 and all(address != config + 0x2B8 for address, _ in memory.reads)
    changed = Memory()
    changed.changed = True
    with pytest.raises(SocialError, match="account_changed_during_probe"):
        probe.read_identity(changed, b"synthetic-image", [tmp_path])
    (tmp_path / "fixture-self_wxyz/db_storage").mkdir(parents=True)
    with pytest.raises(SocialError, match="active_account_directory_not_unique"):
        probe.read_identity(Memory(), b"synthetic-image", [tmp_path])
