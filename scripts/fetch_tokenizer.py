"""Explicitly fetch and verify the framework's tokenizer data for offline startup."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def main() -> None:
    directory = ROOT / "src" / "wechat_agent" / "assets" / "tiktoken"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / hashlib.sha1(URL.encode()).hexdigest()
    if target.exists():
        content = target.read_bytes()
    else:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", URL) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 4 * 1024 * 1024:
                        raise SystemExit("Tokenizer exceeded expected bound")
                content = bytes(data)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise SystemExit("Tokenizer hash mismatch; no resource written")
    target.write_bytes(content)
    distribution = importlib.metadata.distribution("tiktoken")
    license_file = next(p for p in distribution.files if str(p).endswith("dist-info/licenses/LICENSE"))
    (directory / "LICENSE.txt").write_bytes(distribution.locate_file(license_file).read_bytes())
    (directory / "manifest.json").write_text(json.dumps({
        "encoding": "cl100k_base", "tiktoken_version": distribution.version,
        "url": URL, "sha256": SHA256, "cache_key": target.name, "bytes": len(content),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Verified offline tokenizer: {len(content)} bytes; sha256={SHA256}")


if __name__ == "__main__":
    main()
