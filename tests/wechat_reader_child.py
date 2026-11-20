"""Synthetic source for real pipe/Job tests; never imports native integration."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from wechat_reader_worker import serve

from wechat_agent.bridges.wechat.contracts import SourceRecord
from wechat_agent.social.models import SocialError

MODE = sys.argv[1]


class Source:
    def __init__(self, window, expected):
        self.window = window
        self.manifest_failures, self.page_failures = 0, 0

    def manifest(self):
        if MODE == "snapshot-once" and not self.manifest_failures:
            self.manifest_failures += 1
            raise SocialError("source_changed_during_snapshot")
        return {"account_id": "fixture-account", "root_identity": "a" * 64, "shards": {
            "message_0.db": {"identity": "b" * 64, "tail": {"sort_seq": 0, "local_id": 0}, "local_fence": 0}}}

    def page(self, epoch, name, checkpoint):
        if MODE == "snapshot-page-always":
            raise SocialError("source_changed_during_snapshot")
        if MODE == "snapshot-once" and self.page_failures < 2:
            self.page_failures += 1
            raise SocialError("source_changed_during_snapshot")
        if MODE == "late":
            time.sleep(30)
        start = checkpoint["start"]["local_id"]
        return tuple(SourceRecord("fixture-account", epoch, "filehelper", "dm", checkpoint["shard"],
            i, 10, "fixture-sender", 1_800_000_000, 1_800_000_000, "same body")
            for i in range(start + 1, min(start + 32, 70) + 1))

    def close(self):
        pass


if MODE == "ack-failure":
    # Fail the stdout write AFTER a page has been sent and parent has committed.
    import wechat_reader_worker as module

    original = module.encode_frame

    def fail_ack(value):
        if value["result"] == {"accepted": True}:
            raise SocialError("synthetic_lost_ack")
        return original(value)

    module.encode_frame = fail_ack

serve(Source)
