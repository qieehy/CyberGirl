"""Bounded implementation of Tencent's published Weixin bot HTTP protocol."""

import asyncio
import base64
import json
import secrets
from urllib.parse import urlsplit

import httpx

from ...social.models import SocialError
from ..wechat.send_process import unique_object

ORIGIN = "https://ilinkai.weixin.qq.com"
VERSION = "3.0.1/cybergirl-0.1.0"


class ClawBotError(SocialError):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.uncertain = uncertain


def approved_origin(value):
    if not isinstance(value, str):
        raise ClawBotError("clawbot_origin_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".weixin.qq.com")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ClawBotError("clawbot_origin_invalid")
    return "https://" + parsed.hostname


class ClawBotClient:
    def __init__(self, *, transport=None, origin=ORIGIN, token=None):
        self.origin, self.token = approved_origin(origin), token
        self.http = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(40, connect=10),
            limits=httpx.Limits(max_connections=4),
            headers={"Accept-Encoding": "identity", "User-Agent": "Cybergirl/0.1.0"},
        )

    async def request(self, method, path, *, payload=None, params=None, auth=True, origin=None):
        if path not in {"get_bot_qrcode", "get_qrcode_status", "getupdates", "sendmessage"}:
            raise ClawBotError("clawbot_endpoint_denied")
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str((3 << 16) | 1)}
        body = None if payload is None else dict(payload)
        if path != "get_qrcode_status":
            headers.update(
                AuthorizationType="ilink_bot_token",
                **{"X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode()},
            )
        if auth:
            if not self.token:
                raise ClawBotError("clawbot_login_required")
            headers["Authorization"] = "Bearer " + self.token
            body = dict(
                body or {}, base_info=dict(channel_version=VERSION, bot_agent="Cybergirl/0.1.0")
            )
        try:
            async with asyncio.timeout(45):
                async with self.http.stream(
                    method,
                    approved_origin(origin or self.origin) + "/ilink/bot/" + path,
                    headers=headers,
                    params=params,
                    json=body,
                ) as response:
                    if response.status_code in (401, 403):
                        raise ClawBotError("clawbot_session_expired")
                    if not 200 <= response.status_code < 300:
                        raise ClawBotError("clawbot_http_error", uncertain=path == "sendmessage")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise ClawBotError(
                                "clawbot_response_limit", uncertain=path == "sendmessage"
                            )
            data = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if type(data) is not dict:
                raise ValueError()
        except (httpx.HTTPError, TimeoutError):
            raise ClawBotError("clawbot_network_error", uncertain=path == "sendmessage") from None
        except (ValueError, UnicodeError):
            raise ClawBotError(
                "clawbot_response_invalid", uncertain=path == "sendmessage"
            ) from None
        if data.get("ret") == -14 or data.get("errcode") == -14:
            raise ClawBotError("clawbot_session_expired")
        if data.get("ret", 0) not in (0, None) or data.get("errcode", 0) not in (0, None):
            raise ClawBotError("clawbot_api_rejected")
        return data

    async def login_qr(self):
        return await self.request(
            "POST",
            "get_bot_qrcode",
            params={"bot_type": 3},
            payload={"local_token_list": []},
            auth=False,
            origin=ORIGIN,
        )

    async def login_status(self, qrcode, *, origin=None, pin=None):
        params = {"qrcode": qrcode}
        if pin:
            params["verify_code"] = pin
        return await self.request(
            "GET", "get_qrcode_status", params=params, auth=False, origin=origin
        )

    async def updates(self, cursor):
        return await self.request("POST", "getupdates", payload={"get_updates_buf": cursor})

    async def send(self, owner, text, context_token, client_id):
        if not owner or not context_token or not 1 <= len(text) <= 2000:
            raise ClawBotError("clawbot_reply_invalid")
        # One transport attempt. An uncertain response is never automatically retried.
        data = await self.request(
            "POST",
            "sendmessage",
            payload={
                "msg": dict(
                    from_user_id="",
                    to_user_id=owner,
                    client_id=client_id,
                    message_type=2,
                    message_state=2,
                    context_token=context_token,
                    item_list=[dict(type=1, text_item=dict(text=text))],
                )
            },
        )
        if data.get("ret") != 0:
            raise ClawBotError("clawbot_send_unconfirmed", uncertain=True)
        return data

    async def aclose(self):
        await self.http.aclose()
