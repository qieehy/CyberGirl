"""Resolve one selected peer media record; binary keys remain in this child."""

# The pinned profile must activate before native imports.
# ruff: noqa: E402

import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.wechat_42013_profile import activate

activate()
from verify_wechat_accessibility import DLL_SHA, EXE_SHA, Page
from wechat_reader_source import ReaderSource

from wechat_agent.bridges.wechat.contracts import WindowBinding, digest
from wechat_agent.bridges.wechat.keyprobe import verify_page_key
from wechat_agent.bridges.wechat.media import (
    MAX_MEDIA_BYTES,
    decode_image,
    decode_sticker,
    image_data_url,
    media_xml,
    transcribe_voice,
)
from wechat_agent.bridges.wechat.reader import conversation_table
from wechat_agent.bridges.wechat.snapshot import open_memory_snapshot
from wechat_agent.bridges.wechat.textcodec import decode_text
from wechat_agent.bridges.wechat.wcdb_probe import scan_selected_page
from wechat_agent.social.models import SocialError


def database(source, filename):
    path = source.account / "db_storage/message" / filename
    with path.open("rb") as handle:
        page = handle.read(4096)
    key = next((v[1] for v in source.shards.values() if verify_page_key(v[1], page)), None)
    if key is None:
        key, _, _ = scan_selected_page(source.memory, Page, source.pairs, page)
    return open_memory_snapshot(path, key, page[:16])[0]


def read_asset(root, path):
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or not 16 <= path.stat().st_size <= MAX_MEDIA_BYTES:
        raise SocialError("media_path_or_size_denied")
    return path.read_bytes()


def resolve(source, request):
    source.manifest()
    name = request["shard"].split(":", 1)[0]
    if name not in source.shards or type(request["local_id"]) is not int:
        raise SocialError("invalid_media_source")
    path, key, salt, identity = source.shards[name]
    if request["shard"] != name + ":" + digest([identity, hashlib.sha256(salt).hexdigest()]):
        raise SocialError("media_source_changed")
    connection, _ = open_memory_snapshot(path, key, salt)
    try:
        table = conversation_table(connection, source.target_username)
        compression = 'm."WCDB_CT_message_content"'
        row = connection.execute(
            f'''SELECT m.local_type,m.create_time,m.message_content,
            {compression},n.user_name,m.sort_seq FROM "{table}" m JOIN Name2Id n ON n.rowid=m.real_sender_id
            WHERE m.local_id=? AND length(CAST(m.message_content AS BLOB))<=16000''',
            (request["local_id"],),
        ).fetchone()
    finally:
        connection.close()
    if not row or row[4] != source.target_username or row[0] not in {3, 34, 47}:
        raise SocialError("private_peer_media_required")
    if (
        row[1] != request["occurred_at"]
        or row[5] != request["sort_seq"]
        or {3: "image", 34: "voice", 47: "sticker"}[row[0]] != request["kind"]
    ):
        raise SocialError("media_source_changed")
    node = media_xml(decode_text(row[2], row[3]))
    if row[0] == 34:
        connection = database(source, "media_0.db")
        try:
            voice = connection.execute(
                """SELECT v.voice_data FROM VoiceInfo v
                JOIN Name2Id n ON n.rowid=v.chat_name_id WHERE n.user_name=?
                AND v.local_id=? AND v.create_time=? AND length(v.voice_data)<=1048576 LIMIT 1""",
                (source.target_username, request["local_id"], row[1]),
            ).fetchone()
        finally:
            connection.close()
        if not voice:
            raise SocialError("voice_not_cached")
        return dict(
            kind="voice",
            text="[语音转写] " + transcribe_voice(bytes(voice[0]), request["speech_model"]),
        )
    if row[0] == 3:
        image = node.find(".//img")
        if image is None:
            raise SocialError("invalid_media_xml")
        connection = database(source, "message_resource.db")
        try:
            resources = connection.execute(
                """SELECT r.packed_info FROM MessageResourceInfo r
                JOIN ChatName2Id c ON c.rowid=r.chat_id WHERE c.user_name=?
                AND r.message_local_id=? AND r.message_local_type=3 AND r.message_create_time=? LIMIT 2""",
                (source.target_username, request["local_id"], row[1]),
            ).fetchall()
        finally:
            connection.close()
        hashes = {image.get("md5", "").lower()}
        for resource in resources:
            hashes.update(
                b.decode("ascii")
                for b in re.findall(rb"[0-9a-f]{32}", bytes(resource[0] or b"")[:1024])
            )
        hashes = {h for h in hashes if re.fullmatch("[0-9a-f]{32}", h)}
        if not hashes:
            raise SocialError("image_identity_unavailable")
        folder = (
            source.account / "msg/attach" / hashlib.md5(source.target_username.encode()).hexdigest()
        )
        matches = []
        for month in list(folder.iterdir())[:120] if folder.is_dir() else []:
            matches.extend(
                p
                for md5 in sorted(hashes)
                for suffix in (".dat", "_t.dat")
                if (p := month / "Img" / (md5 + suffix)).is_file()
            )
        if not matches:
            raise SocialError("image_not_cached")
        config_dword = struct.unpack("<I", source.memory.read_bytes(source.config + 0x40, 4))[0]
        data = decode_image(
            read_asset(source.account, matches[0]),
            account_id=source.username,
            config_dword=config_dword,
        )
        return dict(
            kind="image", text="[图片] 请根据实际图像内容回复。", image_url=image_data_url(data)
        )
    emoji = node.find(".//emoji")
    if emoji is None or not re.fullmatch("[0-9a-fA-F]{32}", emoji.get("md5", "")):
        raise SocialError("sticker_identity_unavailable")
    # Match only the selected message's exact content hash; never fetch XML URLs.
    wanted = emoji.get("md5").lower()
    entries = 0
    for directory in (
        source.account / "business/emoticon",
        source.account / "cache",
        source.account / "resource",
    ):
        for parent, _, files in os.walk(directory, followlinks=False):
            for filename in files:
                entries += 1
                if entries > 30000:
                    raise SocialError("sticker_cache_bound")
                if wanted in filename.lower():
                    data = read_asset(source.account, Path(parent) / filename)
                    try:
                        if not data.startswith(
                            (b"wxgf", b"GIF8", b"\x89PNG", b"\xff\xd8\xff", b"RIFF")
                        ):
                            config_dword = struct.unpack(
                                "<I", source.memory.read_bytes(source.config + 0x40, 4)
                            )[0]
                            data = decode_sticker(
                                data,
                                account_id=source.username,
                                config_dword=config_dword,
                                cdn_key=emoji.get("aeskey"),
                            )
                        return dict(
                            kind="sticker",
                            text="[表情包] 请结合表情中的文字和动作回复。",
                            image_url=image_data_url(data),
                        )
                    except SocialError:
                        continue
    desc = emoji.get("desc") or emoji.get("attachedtext")
    if desc and len(desc) <= 500:
        return dict(kind="sticker", text="[表情包文字描述，非图像识别] " + desc)
    raise SocialError("sticker_not_cached")


def main():
    request = json.loads(sys.stdin.buffer.readline(16000))
    window = WindowBinding(**request["window"])
    if window.exe_sha256 != EXE_SHA or window.dll_sha256 != DLL_SHA:
        raise SocialError("reader_binary_binding_mismatch")
    source = ReaderSource(window, request["expected"])
    try:
        source.target_username = request["username"]
        if source.target_username == source.username:
            raise SocialError("private_peer_media_required")
        result = resolve(source, request)
        source.validate()
        print(json.dumps(result, ensure_ascii=True))
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(
            json.dumps(
                dict(error=exc.code if isinstance(exc, SocialError) else "media_decode_failed")
            )
        )
        sys.exit(2)
