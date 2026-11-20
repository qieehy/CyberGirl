"""One pinned active account, at most 16 message shards, only filehelper rows.

Native keys and authenticated plaintext snapshots stay in the owned child.
No UIA lease, UI reads, input operations, model requests or source writes.
"""

import hashlib
import time
from dataclasses import asdict

from probe_wechat_account import read_identity
from probe_wechat_database import configured_roots
from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory, Page

from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    digest,
)
from wechat_agent.bridges.wechat.keyprobe import message_database, remote_bytes, verify_page_key
from wechat_agent.bridges.wechat.reader import baseline, conversation_table, forward_guard, read_new
from wechat_agent.bridges.wechat.receive_state import MAX_SHARDS, fingerprint
from wechat_agent.bridges.wechat.snapshot import open_memory_snapshot
from wechat_agent.bridges.wechat.wcdb_probe import config_pairs, scan_selected_page
from wechat_agent.social.models import SocialError


def file_identity(path):
    stat = path.stat()
    if not stat.st_ino:
        raise SocialError("reader_file_identity_unavailable")
    return [stat.st_dev, stat.st_ino]


class ReaderSource:
    target_username = "filehelper"
    target_evidence_ref = "w2a-explicit-filehelper-v1"
    media_enabled = False

    def __init__(self, window, expected):
        fingerprint(expected)
        if window.exe_sha256 != EXE_SHA or window.dll_sha256 != DLL_SHA:
            raise SocialError("reader_binary_binding_mismatch")
        self.memory = NativeMemory(window.pid, hwnd=window.hwnd, created_filetime=window.created_filetime)
        self.shards = {}
        self.manifest_items = {}
        try:
            self.memory.preflight()
            image = DLL.read_bytes()
            self.username, self.account, self.config = read_identity(self.memory, image, configured_roots())
            if hashlib.sha256(self.username.encode()).hexdigest() != expected:
                raise SocialError("reader_account_mismatch")
            self.window = window
            self.pairs = config_pairs(image, self.memory.sections, self.memory.base)
            self.root_identity = self._root_identity()
        except BaseException:
            self.close()
            raise

    def _root_identity(self):
        folder = self.account / "db_storage/message"
        return digest([str(self.account.resolve()).casefold(), file_identity(self.account), file_identity(folder)])

    def validate(self):
        self.memory.validate()
        if remote_bytes(self.memory.read_bytes, self.config + 0x48, maximum=128).decode("ascii") != self.username:
            raise SocialError("reader_account_changed")
        if self._root_identity() != self.root_identity:
            raise SocialError("reader_account_database_replaced")
        for path, _, salt, identity in self.shards.values():
            with path.open("rb") as handle:
                header = handle.read(16)
            if header != salt or file_identity(path) != identity:
                raise SocialError("reader_shard_replaced")

    def manifest(self):
        import re

        self.validate()
        entries = []
        for entry in (self.account / "db_storage/message").iterdir():
            entries.append(entry.name)
            if len(entries) > 256:
                raise SocialError("reader_directory_bound")
        names = sorted(n for n in entries if re.fullmatch(r"message_\d{1,3}\.db", n))
        if not 1 <= len(names) <= MAX_SHARDS:
            raise SocialError("reader_shard_bound")
        if not set(self.shards) <= set(names):
            raise SocialError("reader_shard_disappeared")
        for name in names:
            if name in self.shards:
                continue
            path = message_database(self.account, name)
            identity = file_identity(path)
            with path.open("rb") as handle:
                page = handle.read(4096)
            key = next((key for _, key, _, _ in self.shards.values() if verify_page_key(key, page)), None)
            if key is None:
                key, salt, _ = scan_selected_page(self.memory, Page, self.pairs, page)
            else:
                salt = page[:16]
            self.shards[name] = (path, key, salt, identity)
        result = {}
        for name, (path, key, salt, identity) in self.shards.items():
            if name in self.manifest_items:
                result[name] = self.manifest_items[name]
                continue
            connection, _ = open_memory_snapshot(path, key, salt)
            try:
                try:
                    tail = baseline(connection, self.target_username)
                    fence = forward_guard(connection, self.target_username, Cursor(0, 0), 0)
                except SocialError as exc:
                    if exc.code != "conversation_not_in_selected_shard":
                        raise
                    tail, fence = Cursor(0, 0), 0
                result[name] = {"identity": digest([identity, hashlib.sha256(salt).hexdigest()]),
                                "tail": asdict(tail), "local_fence": fence}
                self.manifest_items[name] = result[name]
            finally:
                connection.close()
        self.validate()
        return {"account_id": self.username, "root_identity": self.root_identity, "shards": result}

    def page(self, epoch, name, checkpoint):
        self.validate()
        if name not in self.shards or set(checkpoint) != {"shard", "start", "local_fence"}:
            raise SocialError("invalid_reader_checkpoint")
        start = Cursor(**checkpoint["start"])
        fence = checkpoint["local_fence"]
        if type(fence) is not int or not start.local_id <= fence <= 2**63 - 1:
            raise SocialError("invalid_local_fence")
        path, key, salt, identity = self.shards[name]
        if checkpoint["shard"] != name + ":" + digest([identity, hashlib.sha256(salt).hexdigest()]):
            raise SocialError("reader_shard_mismatch")
        binding = ConversationBinding(AccountBinding(self.username, epoch, self.username, self.window,
            "w2a-authenticated-reader-v1"), self.target_username, "dm", self.target_evidence_ref)
        connection, _ = open_memory_snapshot(path, key, salt)
        try:
            try:
                conversation_table(connection, self.target_username)
            except SocialError as exc:
                if exc.code == "conversation_not_in_selected_shard" and start == Cursor(0, 0) and not fence:
                    return ()
                raise
            forward_guard(connection, self.target_username, start, fence)
            records = read_new(connection, binding, checkpoint["shard"], start, time.time(), limit=32,
                               media=self.media_enabled)
        finally:
            connection.close()
        self.validate()
        return records

    def echo_baseline(self):
        """Fresh tails, distinct from the immutable discovery baseline cache."""
        self.validate()
        self.manifest()
        result = {}
        for name, (path, key, salt, identity) in self.shards.items():
            connection, _ = open_memory_snapshot(path, key, salt)
            try:
                try:
                    tail = baseline(connection, self.target_username)
                    fence = forward_guard(connection, self.target_username, Cursor(0, 0), 0)
                except SocialError as exc:
                    if exc.code != "conversation_not_in_selected_shard":
                        raise
                    tail, fence = Cursor(0, 0), 0
                result[name] = dict(identity=digest([identity, hashlib.sha256(salt).hexdigest()]),
                                    tail=asdict(tail), local_fence=fence)
            finally:
                connection.close()
        self.validate()
        return dict(shards=result)

    def close(self):
        self.shards.clear()
        self.manifest_items.clear()
        self.memory.close()
