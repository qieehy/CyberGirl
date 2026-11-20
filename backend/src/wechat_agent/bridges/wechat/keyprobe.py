"""Bounded key compatibility helpers; never serialize secrets or scan all accounts.

Modified adaptation of wechatauto-replica db.py, commit
6e603974bbac3bda93d88bbd6995510ae2547f5c (Apache-2.0). Upstream credits
ReadWeixinKey-rev for the cfg extraction approach. See assets/licenses and
docs/WECHAT_DB_SOURCE_NOTICE.md for provenance and substantive changes.
"""

import hashlib
import hmac
import re
import struct
from pathlib import Path

from ...social.models import SocialError

MASTER_PATTERN = bytes.fromhex(
    "83ec404889d64889cb0f57c00f1142100f11024c8bb1c8020000"
    "4883b9d0020000107209488b9bb8020000eb074881c3b8020000"
    "4d85f60f880a0200004983fe10736d4c89761048c746180f0000"
    "000f10030f110648b8"
)
LANDMARK = b"global_config"
PAGE_SIZE = 4096


def xor_material(image):
    if image.count(MASTER_PATTERN) != 1:
        raise SocialError("master_pattern_not_unique")
    offset = image.index(MASTER_PATTERN) + len(MASTER_PATTERN)
    values = []
    for suffix in (bytes.fromhex("488944242048b8"), bytes.fromhex("488944242848b8"),
                   bytes.fromhex("488944243048b8")):
        if image[offset + 8:offset + 15] != suffix:
            raise SocialError("master_instructions_mismatch")
        values.append(image[offset:offset + 8])
        offset += 15
    values.append(image[offset:offset + 8])
    if any(len(v) != 8 for v in values):
        raise SocialError("master_instructions_truncated")
    return b"".join(values)


def config_landmark(data):
    matches, start = [], 0
    while (offset := data.find(LANDMARK, start)) >= 0:
        start = offset + 1
        if offset % 8 == 0 and data[offset + 16:offset + 32] == struct.pack("<QQ", 13, 15):
            matches.append(offset)
    if len(matches) != 1:
        raise SocialError("config_landmark_not_unique")
    return matches[0]


def remote_bytes(read, address, *, maximum):
    header = read(address, 32)
    size, capacity = struct.unpack_from("<QQ", header, 16)
    if not 1 <= size <= maximum or capacity < size or capacity > 1024:
        raise SocialError("remote_string_layout_mismatch")
    if capacity <= 15:
        return header[:size]
    pointer = struct.unpack_from("<Q", header)[0]
    if not 0x10000 <= pointer < 0x800000000000:
        raise SocialError("remote_pointer_invalid")
    return read(pointer, size)


def config_identity(read, section_base, data):
    """Read the active username without touching the encrypted key field."""
    landmark = config_landmark(data)
    # Upstream version-sensitive offsets: never search alternate offsets silently.
    pointer_address = section_base + landmark + 16 - 0x138
    first = struct.unpack("<Q", read(pointer_address, 8))[0]
    if not 0x10000 <= first < 0x800000000000:
        raise SocialError("config_pointer_invalid")
    config = struct.unpack("<Q", read(first + 0x68, 8))[0]
    if not 0x10000 <= config < 0x800000000000:
        raise SocialError("config_pointer_invalid")
    username = remote_bytes(read, config + 0x48, maximum=128)
    if not re.fullmatch(rb"[A-Za-z0-9_-]{1,128}", username):
        raise SocialError("config_identity_unrecognized")
    return username.decode("ascii"), config


def config_identity_and_key(read, section_base, data, material):
    username, config = config_identity(read, section_base, data)
    cipher = remote_bytes(read, config + 0x2B8, maximum=32)
    if len(cipher) != 32 or len(material) != 32:
        raise SocialError("config_key_layout_mismatch")
    return username, bytes(a ^ b for a, b in zip(cipher, material, strict=True)), config


def account_directory(roots, username):
    """Only inspect directory metadata; open no other account databases."""
    matches = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and (child.name == username or re.fullmatch(re.escape(username) + r"_\w{4}", child.name)):
                if (child / "db_storage").is_dir():
                    matches.add(child.resolve())
    if len(matches) != 1:
        raise SocialError("active_account_directory_not_unique")
    return matches.pop()


def verify_page_key(key, page, *, salt=None, page_number=1):
    if len(key) != 32 or len(page) != PAGE_SIZE:
        return False
    salt = page[:16] if salt is None else salt
    if len(salt) != 16:
        return False
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    start = 16 if page_number == 1 else 0
    expected = hmac.digest(mac_key, page[start:PAGE_SIZE - 64] + struct.pack("<I", page_number), "sha512")
    return hmac.compare_digest(expected, page[-64:])


def derive_page_key(master, page):
    if len(master) != 32 or len(page) != PAGE_SIZE or page.startswith(b"SQLite format 3\x00"):
        raise SocialError("unsupported_database_header")
    key = hashlib.pbkdf2_hmac("sha512", master, page[:16], 256000, dklen=32)
    if not verify_page_key(key, page):
        raise SocialError("database_key_mismatch")
    return key


def message_database(account: Path, name="message_0.db"):
    if not re.fullmatch(r"message_\d{1,3}\.db", name):
        raise SocialError("invalid_message_shard")
    path = (account / "db_storage/message" / name).resolve()
    if not path.is_relative_to(account.resolve()) or not path.is_file():
        raise SocialError("message_shard_unavailable")
    return path
