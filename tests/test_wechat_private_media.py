"""Critical media admission, decoding, and failure isolation; no external effects."""

import asyncio
import base64
import io
import json
import struct
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from tests.test_private_auto_reply import build
from tests.test_workbench_private_local import incoming
from wechat_agent.bridges.wechat.media import decode_image, image_data_url, media_xml
from wechat_agent.providers.codex.config import Limits
from wechat_agent.providers.codex.errors import ProviderError
from wechat_agent.providers.codex.provider import build_request
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.service import LiveDraftGenerator


def test_local_image_decode_and_bounded_vision_request():
    import hashlib

    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), "blue").save(buffer, "PNG")
    plain = buffer.getvalue()
    prefix, tail = plain[:32], plain[32:]
    key = hashlib.md5(b"123account").hexdigest()[:16].encode()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    data = b"\x07\x08V2\x08\x07" + struct.pack("<II", 32, len(tail)) + b"\x00"
    data += enc.update(prefix + bytes([16]) * 16) + enc.finalize()
    data += bytes(b ^ 123 for b in tail)
    decoded = decode_image(data, account_id="account", config_dword=123)
    assert decoded == plain
    image = image_data_url(decoded)
    assert base64.b64decode(image.split(",", 1)[1]).startswith(b"\xff\xd8\xff")
    messages = [
        dict(role="system", content="fixture"),
        dict(
            role="user",
            content=[
                dict(type="input_text", text="image"),
                dict(type="input_image", image_url=image),
            ],
        ),
    ]
    assert (
        build_request(messages, None, model="fixture", limits=Limits())["input"][0]["content"][1][
            "type"
        ]
        == "input_image"
    )
    messages[1]["content"][1]["image_url"] = "https://untrusted.example/image.jpg"
    with pytest.raises(ProviderError):
        build_request(messages, None, model="fixture", limits=Limits())
    with pytest.raises(SocialError, match="invalid_media_xml"):
        media_xml("<!DOCTYPE msg><msg/>")


@pytest.mark.parametrize("kind", ["image", "sticker", "voice"])
def test_fresh_peer_media_replies_once_and_failure_keeps_text_running(tmp_path, kind):
    async def run():
        calls = []

        async def model(text, history, *_):
            calls.append(text)
            if len(calls) == 1:
                raise SocialError("media_decode_failed")
            return Decision(ActionKind.REPLY, "fixture reply")

        with Ledger(tmp_path / "media.db") as ledger:
            wb, sender = build(ledger, model)
            incoming(ledger, n=1, kind=kind)  # startup backlog remains excluded
            wb.auto_reply.set_enabled(True)
            wb.auto_reply.tick()
            assert wb.auto_reply.task is None
            incoming(ledger, n=2, kind=kind)
            wb.auto_reply.tick()
            await wb.auto_reply.task
            assert wb.auto_reply.enabled and sender.calls == []
            wb.auto_reply.tick()
            assert len(calls) == 1
            incoming(ledger, n=3)
            wb.auto_reply.tick()
            await wb.auto_reply.task
            assert len(calls) == 2 and len(sender.calls) == 1
            await wb.aclose()

    asyncio.run(run())


def test_image_uses_one_vision_call_and_preserves_draft_send_fence(tmp_path):
    async def run():
        with Ledger(tmp_path / "vision.db") as ledger:
            wb, sender = build(ledger, lambda *_: None)
            calls = []
            buffer = io.BytesIO()
            Image.new("RGB", (10, 10), "red").save(buffer, "PNG")
            image = image_data_url(buffer.getvalue())

            class Resolver:
                async def resolve(self, _):
                    return dict(kind="image", text="[图片]", image_url=image)

            class Provider:
                http_attempts = 0

                async def __call__(self, messages, *, context_key):
                    assert context_key == "epoch"
                    assert messages[1]["content"][1]["image_url"] == image
                    build_request(messages, None, model="fixture", limits=Limits())
                    self.http_attempts += 1
                    calls.append(1)
                    return SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                dict(action="REPLY", reply_text="fixture vision reply")
                            )
                        )
                    )

            provider = Provider()

            async def session():
                return "fixture"

            @asynccontextmanager
            async def app(*_):
                yield SimpleNamespace(
                    provider=provider,
                    tokens=SimpleNamespace(context_key=lambda: "epoch", get_session=session),
                )

            async def catalog(_):
                return SimpleNamespace(contains=lambda _: True, context_key="epoch")

            wb.generator = LiveDraftGenerator(
                ledger,
                "fixture",
                Path("unused"),
                app_factory=app,
                catalog_fetch=catalog,
                media_resolver=Resolver(),
            )
            source = incoming(ledger, kind="image")
            draft = await wb.generate(
                request_id="vision",
                source_message_id=source.message_id,
                expected_message_revision=wb.state.revision(),
                expected_persona_version=1,
            )
            await wb.generation_task
            result = wb.state.draft(draft["draft_id"])
            assert result["state"] == "READY" and len(calls) == 1
            assert (
                result["model_trace"]["inference_calls"]
                == result["model_trace"]["model_http_attempts"]
                == 1
            )
            assert sender.calls == []
            await wb.aclose()

    asyncio.run(run())
