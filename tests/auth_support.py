import asyncio
import base64
import json
from dataclasses import replace

import httpx

from wechat_agent.providers.codex.auth_models import AuthEndpoint, DeviceChallenge, TokenSet


class FakeClock:
    def __init__(self):
        self.now = 1_900_000_000.0
        self.monotonic = 100.0
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.monotonic += seconds
        await asyncio.sleep(0)


def jwt(clock, *, account="h2-synthetic-account", expires=3600, residency=None):
    auth = {"chatgpt_account_id": account}
    if residency is not None:
        auth["chatgpt_data_residency"] = residency
    claims = {"exp": int(clock.now + expires), "https://api.openai.com/auth": auth}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return "h2-synthetic-header." + payload + ".h2-synthetic-signature"


def token_payload(clock, *, refresh="h2-synthetic-refresh-new", account="h2-synthetic-account", expires=3600, residency=None):
    return {"access_token": jwt(clock, account=account, expires=expires, residency=residency),
            "refresh_token": refresh, "token_type": "Bearer", "expires_in": expires}


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


def reply(payload=None, *, status=200, raw=None, headers=None):
    wire = raw if raw is not None else json.dumps(payload or {}).encode()
    return httpx.Response(status, headers={"Content-Type": "application/json", "X-Request-ID": "req_auth", **(headers or {})},
                          stream=ByteStream(wire))


class FakeAuth:
    def __init__(self, clock, *, binding=None):
        self.clock = clock
        self.binding = binding or AuthEndpoint("http://127.0.0.1:8543", local_testing=True).binding
        self.refresh_calls = []
        self.refresh_error = None
        self.refresh_entered = asyncio.Event()
        self.refresh_release = asyncio.Event()
        self.refresh_release.set()
        self.login_entered = asyncio.Event()
        self.login_release = asyncio.Event()
        self.login_release.set()
        self.login_cancelled = asyncio.Event()
        self.closed = False

    def tokens(self, *, expired=False, version="old"):
        return TokenSet(self.binding, f"h2-synthetic-access-{version}", f"h2-synthetic-refresh-{version}",
                        "h2-synthetic-account", self.clock.now + (-10 if expired else 3600))

    async def begin_device_login(self):
        return DeviceChallenge(self.binding.issuer + "/codex/device", "H2-TEST-CODE", "h2-device-secret", 3,
                               self.clock.monotonic + 900)

    async def complete_device_login(self, challenge):
        self.login_entered.set()
        try:
            await self.login_release.wait()
        except asyncio.CancelledError:
            self.login_cancelled.set()
            raise
        return self.tokens(version="login")

    async def refresh(self, previous):
        self.refresh_calls.append(previous)
        self.refresh_entered.set()
        await self.refresh_release.wait()
        if self.refresh_error is not None:
            raise self.refresh_error
        return replace(self.tokens(version="new"), account_id=previous.account_id)

    async def aclose(self):
        self.closed = True


async def ignore_challenge(challenge):
    pass
