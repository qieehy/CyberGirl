"""Bounded, synthetic key compatibility and selected-account checks."""

import ctypes
import hashlib
import hmac
import struct

import pytest

from wechat_agent.bridges.wechat.keyprobe import (
    MASTER_PATTERN,
    account_directory,
    config_identity_and_key,
    derive_page_key,
    message_database,
    remote_bytes,
    verify_page_key,
    xor_material,
)
from wechat_agent.social.models import SocialError


def test_bounded_config_identity_and_key_reject_wrong_layout_or_ambiguous_target(tmp_path):
    material = bytes(range(32))
    image = MASTER_PATTERN + material[:8] + bytes.fromhex("488944242048b8")
    image += material[8:16] + bytes.fromhex("488944242848b8")
    image += material[16:24] + bytes.fromhex("488944243048b8") + material[24:]
    assert xor_material(image) == material
    with pytest.raises(SocialError, match="master_pattern_not_unique"):
        xor_material(image + image)
    base, first, config, cipher_address = 0x10000, 0x20000, 0x30000, 0x40000
    data = b"global_config\0\0\0" + struct.pack("<QQ", 13, 15)
    cipher = bytes(range(32, 64))
    memory = {
        (base + 16 - 0x138, 8): struct.pack("<Q", first),
        (first + 0x68, 8): struct.pack("<Q", config),
        (config + 0x48, 32): b"fixture-self".ljust(16, b"\0") + struct.pack("<QQ", 12, 15),
        (config + 0x2B8, 32): struct.pack("<QQQQ", cipher_address, 0, 32, 32),
        (cipher_address, 32): cipher,
    }
    reads = []

    def read(address, size):
        reads.append((address, size))
        return memory[(address, size)]

    username, master, address = config_identity_and_key(read, base, data, material)
    assert username == "fixture-self" and address == config
    assert master == bytes(a ^ b for a, b in zip(cipher, material, strict=True))
    before = len(reads)
    with pytest.raises(SocialError, match="config_landmark_not_unique"):
        config_identity_and_key(read, base, data + data, material)
    assert len(reads) == before
    memory[(config + 0x48, 32)] = b"x" * 16 + struct.pack("<QQ", 10000, 10000)
    with pytest.raises(SocialError, match="remote_string_layout_mismatch"):
        remote_bytes(read, config + 0x48, maximum=128)
    root = tmp_path / "accounts"
    target = root / "fixture-self_abcd"
    (target / "db_storage/message").mkdir(parents=True)
    (root / "other-account/db_storage/message").mkdir(parents=True)
    (target / "db_storage/message/message_0.db").write_bytes(b"fixture")
    assert account_directory([root], username) == target.resolve()
    assert message_database(target).name == "message_0.db"
    with pytest.raises(SocialError, match="invalid_message_shard"):
        message_database(target, "../../other-account/file.db")
    (root / "fixture-self_wxyz/db_storage").mkdir(parents=True)
    with pytest.raises(SocialError, match="active_account_directory_not_unique"):
        account_directory([root], username)


def test_header_key_verification_rejects_tampering_wrong_account_and_plaintext():
    master, salt = b"fixture master".ljust(32, b"!"), bytes(range(16))
    key = hashlib.pbkdf2_hmac("sha512", master, salt, 256000, dklen=32)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    page = salt + b"fixture ciphertext".ljust(4096 - 16 - 64, b"x")
    page += hmac.digest(mac_key, page[16:] + struct.pack("<I", 1), "sha512")
    assert derive_page_key(master, page) == key
    assert not verify_page_key(key, page[:-1] + bytes([page[-1] ^ 1]))
    assert not verify_page_key(key, page, page_number=2, salt=salt)
    with pytest.raises(SocialError, match="database_key_mismatch"):
        derive_page_key(b"wrong account".ljust(32, b"!"), page)
    with pytest.raises(SocialError, match="unsupported_database_header"):
        derive_page_key(master, b"SQLite format 3\0" + page[16:])


def test_wcdb_probe_matches_only_selected_page_and_stops_at_scan_bounds():
    from scripts.verify_wechat_accessibility import Page
    from wechat_agent.bridges.wechat.wcdb_probe import (
        CONFIG_NAME,
        XOR_MASK,
        config_pairs,
        scan_selected_page,
    )

    image = CONFIG_NAME + b"\0"
    sections = [{"file_offset": 0, "raw_size": len(image), "rva": 4096}]
    pairs = config_pairs(image, sections, 0x50000)
    key, salt = bytes(range(32)), bytes(range(16))
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    page = salt + b"synthetic ciphertext".ljust(4096 - 16 - 64, b"x")
    page += hmac.digest(mac_key, page[16:] + struct.pack("<I", 1), "sha512")
    decoded = b"x'" + key.hex().encode() + b"'"
    blob = bytes(value ^ XOR_MASK[i % len(XOR_MASK)] for i, value in enumerate(decoded))
    node = bytes(16) + pairs[0] + bytes(8) + struct.pack("<Q", 0x20000) + bytes(16)
    chunk = node.ljust(4096, b"\0")

    class Kernel:
        @staticmethod
        def VirtualQueryEx(handle, address, pointer, size):
            if address >= 0x11000:
                return 0
            target = ctypes.cast(pointer, ctypes.POINTER(Page)).contents
            target.BaseAddress, target.RegionSize = 0x10000, 4096
            target.State, target.Type, target.Protect = 0x1000, 0x20000, 4
            return ctypes.sizeof(Page)

    class Memory:
        k, handle = Kernel(), None

        @staticmethod
        def read_bytes(address, size):
            values = {(0x10000, 4096): chunk, (0x10000, 64): node,
                      (0x20088, 40): bytes(8) + struct.pack("<QQ", 0x30000, len(blob)) + bytes(16),
                      (0x30000, len(blob)): blob}
            return values[(address, size)]

    actual, actual_salt, stats = scan_selected_page(Memory(), Page, pairs, page)
    assert actual == key and actual_salt == salt and stats["candidate_count"] == 1
    with pytest.raises(SocialError, match="wcdb_probe_byte_bound"):
        scan_selected_page(Memory(), Page, pairs, page, max_bytes=4095)
    with pytest.raises(SocialError, match="wcdb_probe_time_bound"):
        scan_selected_page(Memory(), Page, pairs, page, seconds=0)
    with pytest.raises(SocialError, match="selected_database_key_not_found"):
        scan_selected_page(Memory(), Page, pairs, page[:-1] + bytes([page[-1] ^ 1]))


def test_authenticated_memory_snapshot_committed_wal_and_source_change(tmp_path, monkeypatch):
    import sqlite3

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from wechat_agent.bridges.wechat import snapshot

    key, salt = bytes(range(32)), bytes(range(16))
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)

    def encrypted_page(user_version):
        plain = bytearray(4096)
        plain[:16] = b"SQLite format 3\0"
        plain[16:24] = struct.pack(">H", 4096) + bytes([2, 2, 80, 64, 32, 32])
        for offset, value in ((24, 1), (28, 1), (40, 1), (44, 4), (56, 1), (60, user_version), (92, 1), (96, 3040000)):
            struct.pack_into(">I", plain, offset, value)
        plain[100] = 13  # Empty sqlite_schema leaf table.
        struct.pack_into(">H", plain, 105, 4016)
        iv = bytes([user_version]) * 16
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        page = salt + cipher.update(bytes(plain[16:4016])) + cipher.finalize() + iv
        page += hmac.digest(mac_key, page[16:] + struct.pack("<I", 1), "sha512")
        return page

    main, committed, pending = encrypted_page(1), encrypted_page(2), encrypted_page(3)
    header = struct.pack(">IIIIII", 0x377F0682, 3007000, 4096, 1, 41, 42)
    sums = snapshot.checksum(header, "<")
    wal = header + struct.pack(">II", *sums)
    for payload, size in ((committed, 1), (pending, 0)):
        frame = struct.pack(">IIII", 1, size, 41, 42)
        sums = snapshot.checksum(frame[:8] + payload, "<", sums)
        wal += frame + struct.pack(">II", *sums) + payload
    path = tmp_path / "encrypted.db"
    path.write_bytes(main)
    (tmp_path / "encrypted.db-wal").write_bytes(wal)
    connection, stats = snapshot.open_memory_snapshot(path, key, salt)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        assert stats == {"wal_valid_frames": 2, "wal_committed_frames": 1, "authenticated_pages": 1}
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden(value)")
    finally:
        connection.close()
    assert path.read_bytes() == main
    assert sorted(p.name for p in tmp_path.iterdir()) == ["encrypted.db", "encrypted.db-wal"]
    with pytest.raises(SocialError, match="wal_frame_checksum"):
        snapshot.decrypt_snapshot(main, wal[:-1] + bytes([wal[-1] ^ 1]), key, salt)
    with pytest.raises(SocialError, match="snapshot_page_hmac_failed"):
        snapshot.decrypt_snapshot(main[:-1] + bytes([main[-1] ^ 1]), b"", key, salt)
    with pytest.raises(SocialError, match="wal_incomplete_or_bound"):
        snapshot.committed_frames(wal[:-1])
    preallocated = wal + bytes(4120) + b"old non-frame tail"
    assert snapshot.committed_frames(preallocated) == snapshot.committed_frames(wal)
    with pytest.raises(SocialError, match="snapshot_format_or_bound"):
        snapshot.decrypt_snapshot(main, wal, key, salt[:15])
    extra_frame = struct.pack(">IIII", 1, 2, 41, 42)
    extra_sums = snapshot.checksum(extra_frame[:8] + committed, "<", snapshot.checksum(header, "<"))
    extra_wal = wal[:32] + extra_frame + struct.pack(">II", *extra_sums) + committed
    with pytest.raises(SocialError, match="snapshot_missing_page"):
        snapshot.decrypt_snapshot(main, extra_wal, key, salt)
    original = snapshot._bounded_read
    count = 0

    def changing_read(path, bound):
        nonlocal count
        data = original(path, bound)
        count += 1
        return data if count < 3 else data[:-1] + bytes([data[-1] ^ 1])

    monkeypatch.setattr(snapshot, "_bounded_read", changing_read)
    with pytest.raises(SocialError, match="source_changed_during_snapshot"):
        snapshot.stable_encrypted_copy(path)


def test_selected_reader_boundary_mapping_and_unknown_sender_do_not_advance_ledger(tmp_path):
    import sqlite3

    from tests.test_wechat_bridge import BINDING, NOW, bind
    from wechat_agent.bridges.wechat.contracts import Cursor
    from wechat_agent.bridges.wechat.reader import baseline, read_new
    from wechat_agent.social.ledger import Ledger

    connection = sqlite3.connect(":memory:")
    table = "Msg_" + hashlib.md5(b"filehelper", usedforsecurity=False).hexdigest()
    connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
    connection.execute("INSERT INTO Name2Id VALUES('fixture-sender')")
    connection.execute(f'CREATE TABLE "{table}"(local_id INTEGER,local_type INTEGER,real_sender_id INTEGER,create_time REAL,message_content TEXT,sort_seq INTEGER)')
    connection.execute(f'INSERT INTO "{table}" VALUES(1,1,1,?,?,10)', (NOW, "old baseline"))
    start = baseline(connection, "filehelper")
    assert start == Cursor(10, 1)
    connection.executemany(f'INSERT INTO "{table}" VALUES(?,1,1,?,?,10)', [(2, NOW, "new"), (3, NOW, "new")])
    with Ledger(tmp_path / "reader.db", now=lambda: NOW) as ledger:
        store = bind(ledger)
        # New explicit baseline fixture uses a separate shard.
        store.baseline(BINDING, "selected", start)
        for expected in (2, 3):
            records = read_new(connection, BINDING, "selected", start, NOW, limit=1)
            assert len(records) == 1 and records[0].local_id == expected
            result = store.ingest(BINDING, "selected", start, records)
            start = result["cursor"]
        assert ledger.counts()["inbox"] == 2 and ledger.counts()["outbox"] == 0
        connection.execute(f'INSERT INTO "{table}" VALUES(4,1,999,?,?,10)', (NOW, "unknown sender"))
        with pytest.raises(SocialError, match="unknown_shard_sender"):
            read_new(connection, BINDING, "selected", start, NOW)
        assert store.cursor(BINDING, "selected") == Cursor(10, 3)
        connection.execute(f'UPDATE "{table}" SET real_sender_id=1,message_content=? WHERE local_id=4', (b"\x28\xb5\x2f\xfd",))
        with pytest.raises(SocialError, match="text_encoding_unsupported"):
            read_new(connection, BINDING, "selected", start, NOW)
        assert store.cursor(BINDING, "selected") == Cursor(10, 3)
    connection.close()
