"""Bounded local media decoding. No networking, UI input or persistent keys."""

import base64
import hashlib
import io
import struct
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...social.models import SocialError

MEDIA_TYPES = {3: ("image", "[图片]"), 34: ("voice", "[语音]"), 47: ("sticker", "[表情包]")}
MAX_MEDIA_BYTES = 12 * 1024 * 1024


def media_xml(text):
    if not isinstance(text, str) or len(text) > 16000 or "<!" in text:
        raise SocialError("invalid_media_xml")
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        raise SocialError("invalid_media_xml") from None


def decode_image(data, *, account_id, config_dword):
    if not 16 <= len(data) <= MAX_MEDIA_BYTES:
        raise SocialError("media_size_limit")
    if data.startswith(b"\x07\x08V2\x08\x07"):
        aes_size, xor_size = struct.unpack_from("<II", data, 6)
        encrypted_size = (aes_size // 16 + 1) * 16
        if aes_size < 1 or 15 + encrypted_size + xor_size > len(data):
            raise SocialError("invalid_image_container")
        key = hashlib.md5((str(config_dword) + account_id).encode()).hexdigest()[:16].encode()
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        prefix = dec.update(data[15 : 15 + encrypted_size]) + dec.finalize()
        pad = prefix[-1]
        if not 1 <= pad <= 16 or prefix[-pad:] != bytes([pad]) * pad:
            raise SocialError("image_key_unavailable")
        split = len(data) - xor_size
        data = (
            prefix[:-pad]
            + data[15 + encrypted_size : split]
            + bytes(b ^ (config_dword & 255) for b in data[split:])
        )
    return data


def decode_sticker(data, *, account_id, config_dword, cdn_key=None):
    if not data or len(data) > MAX_MEDIA_BYTES or len(data) % 16:
        raise SocialError("invalid_sticker_container")
    key = hashlib.md5((str(config_dword) + account_id + "EMOTICON").encode()).digest()
    candidates = [(key, key)]
    if isinstance(cdn_key, str) and len(cdn_key) == 32:
        try:
            cdn = bytes.fromhex(cdn_key)
            candidates.extend(((cdn, bytes(16)), (cdn, cdn)))
        except ValueError:
            pass
    for key, iv in candidates:
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = dec.update(data) + dec.finalize()
        pad = plain[-1]
        if (
            1 <= pad <= 16
            and plain[-pad:] == bytes([pad]) * pad
            and plain.startswith((b"wxgf", b"GIF8", b"\x89PNG", b"\xff\xd8\xff", b"RIFF"))
        ):
            return plain[:-pad]
    raise SocialError("sticker_key_unavailable")


def image_data_url(data):
    """Normalize locally, strip metadata and sample animation at up to three frames."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 25_000_000
    try:
        if data.startswith(b"wxgf"):
            import av

            offset = data.find(b"\x00\x00\x00\x01\x40\x01", 4, 4096)
            if offset < 0:
                raise SocialError("image_format_unsupported")
            frames = []
            with av.open(io.BytesIO(data[offset:]), format="hevc") as container:
                for index, decoded in enumerate(container.decode(video=0)):
                    if decoded.width * decoded.height > Image.MAX_IMAGE_PIXELS:
                        raise SocialError("image_pixel_limit")
                    if index in {0, 10, 20}:
                        frame = decoded.to_image().convert("RGB")
                        frame.thumbnail((1024, 1024))
                        frames.append(frame)
                    if index >= 20:
                        break
            if not frames:
                raise SocialError("image_format_unsupported")
        else:
            frames = _raster_frames(data, Image)
        canvas = Image.new(
            "RGB", (sum(f.width for f in frames), max(f.height for f in frames)), "white"
        )
        left = 0
        for frame in frames:
            canvas.paste(frame, (left, 0))
            left += frame.width
        output = io.BytesIO()
        canvas.save(output, "JPEG", quality=85)
        if output.tell() > 2 * 1024 * 1024:
            raise SocialError("media_size_limit")
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise SocialError("image_format_unsupported") from None


def _raster_frames(data, Image):
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise SocialError("image_pixel_limit")
        count = min(getattr(image, "n_frames", 1), 120)
        frames = []
        for index in sorted({0, count // 2, count - 1}):
            image.seek(index)
            frame = image.convert("RGB")
            frame.thumbnail((1024, 1024))
            frames.append(frame)
        return frames


def transcribe_voice(data, model_path):
    """SILK -> 16k PCM -> local Whisper; neither raw audio nor keys leave this process."""
    import numpy as np
    import pysilk
    from faster_whisper import WhisperModel

    if not data.startswith(b"\x02#!SILK_V3") or len(data) > 1024 * 1024:
        raise SocialError("voice_format_unsupported")
    # Validate duration before decoding (each WeChat SILK packet is 20ms).
    offset = 10
    packets = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise SocialError("invalid_voice_data")
        size = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2 + size
        packets += 1
        if size > 4096 or packets > 3100 or offset > len(data):
            raise SocialError("voice_duration_limit")
    output = io.BytesIO()
    pysilk.decode(io.BytesIO(data), output, 16000)
    samples = np.frombuffer(output.getvalue(), dtype=np.int16).astype(np.float32) / 32768
    if not 1 <= len(samples) <= 16000 * 62:
        raise SocialError("voice_duration_limit")
    model = WhisperModel(
        str(model_path), device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True
    )
    segments, _ = model.transcribe(samples, language="zh", beam_size=3, vad_filter=True)
    text = "".join(segment.text for segment in segments).strip()
    if not text or len(text) > 4000:
        raise SocialError("voice_transcription_empty")
    return text
