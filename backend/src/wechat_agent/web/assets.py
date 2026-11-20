"""Serve only declared frontend build outputs; never expose source or runtime data."""

import json
import os
from pathlib import Path


def web_root():
    override = os.environ.get("WECHAT_AGENT_WEB_DIR")
    return (
        Path(override).resolve() if override else Path(__file__).resolve().parents[1] / "assets/web"
    )


def static_routes(page="sandbox"):
    root = web_root()
    manifest = root / "manifest.json"
    if not manifest.is_file():
        return {}  # API-only development does not require a frontend build.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != 1 or type(data.get("files")) is not dict:
        raise ValueError("Invalid frontend build manifest")
    result = {}
    for url, item in data["files"].items():
        filename = item["file"]
        target = (root / filename).resolve()
        if (
            not isinstance(url, str)
            or not url.startswith("/")
            or "?" in url
            or not target.is_relative_to(root.resolve())
            or not isinstance(item["type"], str)
        ):
            raise ValueError("Invalid frontend build path")
        result[url] = (filename, item["type"])
    if "/" + page in result:
        result["/"] = result["/" + page]
    return result
