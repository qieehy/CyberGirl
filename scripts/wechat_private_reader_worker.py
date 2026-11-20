"""Explicit selected private target; child has no ledger/model/UI-input capability."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-scope-file", type=Path, required=True)
    parser.add_argument("--scope-sha256", required=True)
    parser.add_argument("--media", action="store_true")
    args = parser.parse_args()
    raw_scope = args.private_scope_file.read_bytes()
    if hashlib.sha256(raw_scope).hexdigest() != args.scope_sha256:
        raise SystemExit(2)
    from scripts.wechat_42013_profile import activate
    activate()
    from wechat_agent.bridges.wechat.private_receive import private_scope
    from wechat_agent.bridges.wechat.send_process import unique_object
    scope = private_scope(json.loads(raw_scope, object_pairs_hook=unique_object))
    from wechat_reader_source import ReaderSource

    from scripts.wechat_reader_worker import serve
    from wechat_agent.social.models import SocialError

    class PrivateSource(ReaderSource):
        target_username = scope["username"]
        target_evidence_ref = scope["evidence_ref"]
        media_enabled = args.media

        def __init__(self, window, expected):
            if expected != scope["account_fingerprint"]:
                raise SocialError("private_reader_identity_mismatch")
            super().__init__(window, expected)
            if self.username == self.target_username:
                self.close()
                raise SocialError("private_target_identifier_denied")

        def manifest(self):
            return dict(super().manifest(), target_username=self.target_username)

    serve(PrivateSource)
except BaseException:
    raise SystemExit(2) from None
