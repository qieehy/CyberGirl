"""Two explicitly selected shards, keys/snapshots in memory, no polling/cache."""

import hashlib
import time

from wechat_agent.bridges.wechat.contracts import AccountBinding, ConversationBinding, digest
from wechat_agent.bridges.wechat.keyprobe import message_database, remote_bytes
from wechat_agent.bridges.wechat.reader import baseline, read_new
from wechat_agent.bridges.wechat.snapshot import open_memory_snapshot
from wechat_agent.bridges.wechat.wcdb_probe import config_pairs, scan_selected_page
from wechat_agent.social.models import SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, NativeMemory, Page
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, NativeMemory, Page


class FilehelperSource:
    def __init__(self, window, fingerprint):
        self.memory = NativeMemory(window.pid, hwnd=window.hwnd, created_filetime=window.created_filetime)
        self.shards = {}
        self.stats = {}
        try:
            self.memory.preflight()
            image = DLL.read_bytes()
            username, account, self.config = read_identity(self.memory, image, configured_roots())
            if hashlib.sha256(username.encode()).hexdigest() != fingerprint:
                raise SocialError("source_account_evidence_mismatch")
            self.username = username
            pairs = config_pairs(image, self.memory.sections, self.memory.base)
            for name in ("message_0.db", "message_1.db"):
                path = message_database(account, name)
                with path.open("rb") as handle:
                    page = handle.read(4096)
                key, salt, stats = scan_selected_page(self.memory, Page, pairs, page)
                self.shards[name] = (path, key, salt, path.stat().st_ino)
                self.stats[name] = stats
            epoch = digest({name: hashlib.sha256(item[2]).hexdigest() for name, item in self.shards.items()})
            self.binding = ConversationBinding(AccountBinding(username, epoch, username, window, "w2b5-two-selected-shards-hmac"),
                                               "filehelper", "dm", "w2b4-native-current-page")
            self.validate()
        except BaseException:
            self.close()
            raise

    def validate(self):
        self.memory.validate()
        if remote_bytes(self.memory.read_bytes, self.config + 0x48, maximum=128).decode("ascii") != self.username:
            raise SocialError("source_account_changed")
        for path, _, _, inode in self.shards.values():
            if path.stat().st_ino != inode:
                raise SocialError("selected_shard_replaced")

    def _query(self, function):
        self.validate()
        result = {}
        snapshot_stats = {}
        for name, (path, key, salt, _) in self.shards.items():
            connection, stats = open_memory_snapshot(path, key, salt)
            try:
                result[name] = function(connection, name)
                snapshot_stats[name] = dict(stats)
            finally:
                connection.close()
        self.validate()
        self.latest_snapshot_stats = snapshot_stats
        return result

    def baselines(self):
        return self._query(lambda connection, _: baseline(connection, "filehelper"))

    def records(self, starts):
        if set(starts) != set(self.shards):
            raise SocialError("source_baseline_shards_mismatch")
        batches = self._query(lambda connection, name: read_new(connection, self.binding, name, starts[name], time.time()))
        if any(len(batch) >= 100 for batch in batches.values()):
            raise SocialError("echo_read_not_complete_within_bound")
        return tuple(record for batch in batches.values() for record in batch)

    def close(self):
        self.shards.clear()
        self.memory.close()
