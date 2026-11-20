"""Owner-protected session storage; no credentials or context tokens in diagnostics."""

import json
import os
import tempfile
from pathlib import Path

from ...providers.codex.protection import WindowsProtection
from ...social.models import SocialError
from .client import approved_origin


class SessionVault:
    def __init__(self, path, *, protection=None):
        self.path, self.protection = Path(path), protection or WindowsProtection()

    def load(self):
        if not self.path.is_file():
            return None
        if self.path.stat().st_size > 256 * 1024:
            raise SocialError("clawbot_session_invalid")
        try:
            data = json.loads(self.protection.unprotect(self.path.read_bytes()))
            if (
                set(data) != {"provider", "token", "bot_id", "owner_id", "origin"}
                or data["provider"] != "weixin-oc"
            ):
                raise ValueError()
            for key in ("token", "bot_id", "owner_id"):
                if not isinstance(data[key], str) or not 1 <= len(data[key]) <= (
                    8192 if key == "token" else 128
                ):
                    raise ValueError()
            approved_origin(data["origin"])
            return data
        except (ValueError, TypeError, KeyError):
            raise SocialError("clawbot_session_invalid") from None

    def save(self, token, bot_id, owner_id, origin):
        data = dict(
            provider="weixin-oc",
            token=token,
            bot_id=bot_id,
            owner_id=owner_id,
            origin=approved_origin(origin),
        )
        encoded = self.protection.protect(json.dumps(data).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="session-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def seal(self, value):
        return self.protection.protect(value.encode())

    def open(self, value):
        return self.protection.unprotect(value).decode()
