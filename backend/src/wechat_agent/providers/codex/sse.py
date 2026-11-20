"""Bounded SSE decoding from raw bytes, independent of HTTP or model semantics."""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from .config import Limits
from .errors import ErrorCode, ProviderError


@dataclass(frozen=True, slots=True)
class Frame:
    event: str
    data: str


class SSEDecoder:
    """UTF-8 lines, CR/LF/CRLF, comments, BOM and multi-line data per WHATWG.

    EOF never dispatches an unterminated frame. No reconnect or event replay.
    """

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self._line = bytearray()
        self._data: list[str] = []
        self._event = ""
        self._frame_bytes = 0
        self._stream_bytes = 0
        self._skip_lf = False
        self._first_line = True

    def feed(self, chunk: bytes) -> Iterator[Frame]:
        self._stream_bytes += len(chunk)
        if self._stream_bytes > self.limits.stream_bytes:
            raise ProviderError(ErrorCode.LIMIT)
        for byte in chunk:
            if self._skip_lf:
                self._skip_lf = False
                if byte == 10:
                    continue
            if byte in (10, 13):
                self._skip_lf = byte == 13
                frame = self._finish_line()
                if frame is not None:
                    yield frame
            else:
                self._line.append(byte)
                if len(self._line) > self.limits.line_bytes:
                    raise ProviderError(ErrorCode.LIMIT)

    def _finish_line(self) -> Frame | None:
        self._frame_bytes += len(self._line) + 1
        if self._frame_bytes > self.limits.event_bytes:
            raise ProviderError(ErrorCode.LIMIT)
        try:
            line = self._line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ProviderError(ErrorCode.PROTOCOL) from None
        self._line.clear()
        if self._first_line:
            line = line.removeprefix("\ufeff")
            self._first_line = False
        if not line:
            frame = Frame(self._event or "message", "\n".join(self._data)) if self._data else None
            self._data.clear()
            self._event = ""
            self._frame_bytes = 0
            return frame
        if line.startswith(":"):
            return None
        name, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        return None


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _no_nonfinite(value: str) -> None:
    raise ValueError("Non-finite JSON number")


async def decode_events(chunks: AsyncIterator[bytes], limits: Limits) -> AsyncIterator[dict]:
    decoder = SSEDecoder(limits)
    count = 0
    async for chunk in chunks:
        for frame in decoder.feed(chunk):
            count += 1
            if count > limits.events:
                raise ProviderError(ErrorCode.LIMIT)
            try:
                event = json.loads(frame.data, object_pairs_hook=_no_duplicate_keys,
                                   parse_constant=_no_nonfinite)
            except (ValueError, RecursionError):
                raise ProviderError(ErrorCode.PROTOCOL) from None
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ProviderError(ErrorCode.PROTOCOL)
            if frame.event not in {"message", event["type"]}:
                raise ProviderError(ErrorCode.PROTOCOL)
            yield event
