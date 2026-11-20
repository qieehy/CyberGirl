"""Explicit application proxy; synthetic/test transports never leave their test route."""

import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ErrorCode, ProviderError

PROXY_ENV = "WECHAT_AGENT_PROXY"
LOCAL_CONFIG = "wechat-agent.local.toml"


def _configured_proxy() -> str:
    # An explicitly empty environment override selects direct access.
    if PROXY_ENV in os.environ:
        return os.environ[PROXY_ENV]
    try:
        with (Path.cwd() / LOCAL_CONFIG).open("rb") as handle:
            data = handle.read(16 * 1024 + 1)
    except FileNotFoundError:
        return ""
    except OSError:
        raise ProviderError(ErrorCode.INPUT) from None
    if len(data) > 16 * 1024:
        raise ProviderError(ErrorCode.INPUT)
    try:
        network = tomllib.loads(data.decode("utf-8-sig")).get("network", {})
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise ProviderError(ErrorCode.INPUT) from None
    if not isinstance(network, dict) or not isinstance(network.get("proxy", ""), str):
        raise ProviderError(ErrorCode.INPUT)
    return network.get("proxy", "")


def application_proxy(*, local_testing: bool, injected_transport: bool = False) -> str | None:
    if local_testing or injected_transport:
        return None
    value = _configured_proxy().strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "::1"}
                 and parsed.port is not None and parsed.port > 0
                 and parsed.username is None and parsed.password is None
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ProviderError(ErrorCode.INPUT)
    return value.rstrip("/")
