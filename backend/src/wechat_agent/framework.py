"""Prepare the installed framework without implicit downloads or content logging."""

import hashlib
import os
import threading
from pathlib import Path

from loguru import logger

from .providers.codex.errors import ErrorCode, ProviderError

_ASSET_LOCK = threading.RLock()
_CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def prepare_framework() -> None:
    """Call before importing Runtime/memory; uses tiktoken's supported cache setting.

    The immutable tokenizer registry may then be shared normally. The temporary
    environment setting is restored before any application tasks are created.
    """
    directory = Path(__file__).parent / "assets" / "tiktoken"
    try:
        data = (directory / _CACHE_KEY).read_bytes()
    except OSError:
        raise ProviderError(ErrorCode.STORAGE) from None
    if hashlib.sha256(data).hexdigest() != _SHA256:
        raise ProviderError(ErrorCode.STORAGE)
    with _ASSET_LOCK:
        previous = os.environ.get("TIKTOKEN_CACHE_DIR")
        try:
            os.environ["TIKTOKEN_CACHE_DIR"] = str(directory)
            import tiktoken

            tiktoken.get_encoding("cl100k_base")
        finally:
            if previous is None:
                os.environ.pop("TIKTOKEN_CACHE_DIR", None)
            else:
                os.environ["TIKTOKEN_CACHE_DIR"] = previous
    # The library logs input prefixes before emitting events. Suppress those
    # module trees at the application boundary; only safe event summaries leave it.
    for module in ("runtime", "memory", "tools"):
        logger.disable(module)


prepare_framework()

# Keep all eager framework imports after offline tokenizer initialization.
from errors import LLMError, ToolRoutingError  # noqa: E402, F401
from errors import MemoryError as FrameworkMemoryError  # noqa: E402, F401
from memory import BufferMemory, MemoryManager  # noqa: E402, F401
from runtime import (  # noqa: E402, F401
    Event,
    LoopGuard,
    LoopPolicy,
    RunStatus,
    Runtime,
    SessionCoordinator,
)
from tools import Executor, ToolCallValidator, ToolRegistry  # noqa: E402, F401
from tools.router import ToolRoute  # noqa: E402, F401
