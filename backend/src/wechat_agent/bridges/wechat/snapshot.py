"""Authenticated SQLCipher4 + committed WAL snapshot, entirely in memory.

Fixed 4096-byte pages/80-byte reserve only; not a general SQLCipher driver.
Format references: Zetetic SQLCipher Design and sqlite.org/fileformat2.html.
"""

import hashlib
import hmac
import sqlite3
import struct
import time
from pathlib import Path

from ...social.models import SocialError

PAGE = 4096
MAX_DB = 128 * 1024 * 1024
MAX_WAL = 32 * 1024 * 1024


def checksum(data, endian, previous=(0, 0)):
    first, second = previous
    if len(data) % 8:
        raise SocialError("wal_checksum_length")
    for left, right in struct.iter_unpack(endian + "II", data):
        first = (first + left + second) & 0xFFFFFFFF
        second = (second + right + first) & 0xFFFFFFFF
    return first, second


def committed_frames(wal):
    if not wal:
        return {}, None, {"wal_valid_frames": 0, "wal_committed_frames": 0}
    if len(wal) < 32 or len(wal) > MAX_WAL:
        raise SocialError("wal_incomplete_or_bound")
    magic, version, size = struct.unpack_from(">III", wal)
    if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or size != PAGE:
        raise SocialError("wal_header_unsupported")
    endian = "<" if magic == 0x377F0682 else ">"
    sums = checksum(wal[:24], endian)
    if sums != struct.unpack_from(">II", wal, 24):
        raise SocialError("wal_header_checksum")
    pending, committed, pages, valid, commit_index = {}, {}, None, 0, 0
    complete_end = 32 + ((len(wal) - 32) // (PAGE + 24)) * (PAGE + 24)
    stale_tail = False
    for offset in range(32, complete_end, PAGE + 24):
        frame = wal[offset:offset + 24]
        if frame[8:16] != wal[16:24]:
            stale_tail = True
            break  # Old-generation tail after a WAL reset; never apply it.
        number, commit_size = struct.unpack_from(">II", frame)
        if not 1 <= number <= MAX_DB // PAGE or commit_size > MAX_DB // PAGE:
            raise SocialError("wal_page_bound")
        page = wal[offset + 24:offset + 24 + PAGE]
        sums = checksum(frame[:8] + page, endian, sums)
        if sums != struct.unpack_from(">II", frame, 16):
            raise SocialError("wal_frame_checksum")
        valid += 1
        pending[number] = page
        if commit_size:
            committed.update(pending)
            pending.clear()
            pages, commit_index = commit_size, valid
            committed = {n: p for n, p in committed.items() if n <= pages}
    # Some clients preallocate the WAL to 4 MiB, leaving a non-frame-sized old
    # tail. Ignore it ONLY after an earlier complete frame proves a stale salt.
    if complete_end != len(wal) and not stale_tail:
        raise SocialError("wal_incomplete_or_bound")
    return committed, pages, {"wal_valid_frames": valid, "wal_committed_frames": commit_index}


def _stamp(path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def _bounded_read(path, maximum):
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise SocialError("snapshot_size_bound")
    return data


def stable_encrypted_copy(path):
    """Reject active changes; no automatic retry or filesystem plaintext cache."""
    wal_path = Path(str(path) + "-wal")
    journal = Path(str(path) + "-journal")
    if journal.exists() and journal.stat().st_size:
        raise SocialError("rollback_journal_unsupported")
    before = _stamp(path), _stamp(wal_path)
    database = _bounded_read(path, MAX_DB)
    wal = _bounded_read(wal_path, MAX_WAL) if before[1] is not None else b""
    if (_stamp(path), _stamp(wal_path)) != before:
        raise SocialError("source_changed_during_snapshot")
    # Windows timestamps can lag writes on long-lived handles. Compare content
    # again as well, including the WAL header/generation and committed tail.
    for source, original, maximum in ((path, database, MAX_DB), (wal_path, wal, MAX_WAL)):
        if source == wal_path and before[1] is None:
            continue
        if hashlib.sha256(_bounded_read(source, maximum)).digest() != hashlib.sha256(original).digest():
            raise SocialError("source_changed_during_snapshot")
    if (_stamp(path), _stamp(wal_path)) != before or (journal.exists() and journal.stat().st_size):
        raise SocialError("source_changed_during_snapshot")
    return database, wal


def decrypt_snapshot(database, wal, key, salt, *, deadline=None):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not database or len(database) > MAX_DB or len(database) % PAGE or len(key) != 32 or len(salt) != 16:
        raise SocialError("snapshot_format_or_bound")
    frames, count, stats = committed_frames(wal)
    count = count or len(database) // PAGE
    if not 1 <= count <= MAX_DB // PAGE:
        raise SocialError("snapshot_page_bound")
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    output = bytearray(count * PAGE)
    for number in range(1, count + 1):
        if deadline is not None and time.monotonic() >= deadline:
            raise SocialError("snapshot_time_bound")
        offset = (number - 1) * PAGE
        page = frames.get(number, database[offset:offset + PAGE])
        if len(page) != PAGE:
            raise SocialError("snapshot_missing_page")
        start = 16 if number == 1 else 0
        expected = hmac.digest(mac_key, page[start:PAGE - 64] + struct.pack("<I", number), "sha512")
        if not hmac.compare_digest(expected, page[-64:]):
            raise SocialError("snapshot_page_hmac_failed")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(page[PAGE - 80:PAGE - 64])).decryptor()
        plain = decryptor.update(page[start:PAGE - 80]) + decryptor.finalize()
        output[offset:offset + PAGE] = (b"SQLite format 3\0" if number == 1 else b"") + plain + bytes(80)
    if output[16:18] != struct.pack(">H", PAGE) or output[20] != 80 or output[18] not in (1, 2) or output[19] not in (1, 2):
        raise SocialError("decrypted_header_unsupported")
    # Only the private in-memory copy is changed; it has no on-disk WAL.
    output[18:20] = b"\1\1"
    output[28:32] = struct.pack(">I", count)
    stats["authenticated_pages"] = count
    return output, stats


def open_memory_snapshot(path, key, salt):
    database, wal = stable_encrypted_copy(path)
    if database[:16] != salt:
        raise SocialError("database_epoch_changed")
    plain, stats = decrypt_snapshot(database, wal, key, salt, deadline=time.monotonic() + 30)
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(plain)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.row_factory = sqlite3.Row
        return connection, stats
    except BaseException:
        connection.close()
        raise
    finally:
        plain[:] = bytes(len(plain))
