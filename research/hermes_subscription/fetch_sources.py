"""Fetch pinned public source as text for review; never import or run Hermes."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request


COMMIT = "e82bf01eec9bb58848209b07f0bc7d8895a39f9b"
TREE = "6f036b475cb7e5a193c16e4fdfbef93a6e3dfe03"
ROOT = Path(__file__).resolve().parent / "sources" / COMMIT
FILES = (
    "LICENSE",
    "pyproject.toml",
    "hermes_cli/auth_constants.py",
    "hermes_cli/auth_codex.py",
    "hermes_cli/auth_codex_browser.py",
    "hermes_cli/providers.py",
    "hermes_cli/codex_models.py",
    "agent/codex_headers.py",
    "agent/codex_runtime.py",
    "agent/codex_responses_adapter.py",
    "agent/transports/base.py",
    "agent/transports/codex.py",
    "tests/hermes_cli/test_auth_codex_provider.py",
    "tests/hermes_cli/test_auth_codex_browser_login.py",
    "tests/hermes_cli/test_auth_codex_bounded_body.py",
    "tests/agent/test_codex_responses_adapter.py",
    "tests/agent/test_codex_stream_supersession.py",
    "tests/agent/test_codex_stream_rotating_item_ids.py",
)


def get(url: str, limit: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "WeChatAgent-pinned-source-review"}
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Public source exceeds review download limit")
    return data


def save_new_or_identical(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to replace different source: {path.name}")


def main() -> None:
    tree = json.loads(get(
        f"https://api.github.com/repos/NousResearch/hermes-agent/git/trees/{TREE}?recursive=1",
        20_000_000,
    ))
    if tree.get("truncated") or tree.get("sha") != TREE:
        raise ValueError("Incomplete or mismatched upstream tree")
    entries = {entry["path"]: entry for entry in tree["tree"]}

    def fetch(path: str) -> dict[str, object]:
        entry = entries[path]
        if entry["type"] != "blob":
            raise ValueError("Expected a source blob")
        url = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{COMMIT}/{path}"
        destination = ROOT / (path + ".source.txt")
        data = destination.read_bytes() if destination.exists() else get(url, 2_000_000)
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != entry["sha"]:
            raise ValueError(f"Upstream blob mismatch: {path}")
        data.decode("utf-8")
        save_new_or_identical(destination, data)
        return {
            "path": path,
            "local_path": destination.relative_to(ROOT).as_posix(),
            "url": url,
            "blob_sha1": blob,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    # Fetch the upstream license before collecting implementation samples.
    records = [fetch("LICENSE")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        records.extend(executor.map(fetch, FILES[1:]))
    manifest = {
        "repository": "https://github.com/NousResearch/hermes-agent",
        "commit": COMMIT,
        "tree": TREE,
        "review_date": "2026-10-26",
        "review_timezone": "Asia/Shanghai",
        "note": "Static text snapshots; upstream code and tests were not executed.",
        "files": sorted(records, key=lambda record: str(record["path"])),
    }
    manifest_path = ROOT / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_bytes())
        if previous["commit"] != COMMIT or previous["tree"] != TREE:
            raise ValueError("Refusing to replace a different source manifest")
        records_by_path = {record["path"]: record for record in manifest["files"]}
        if any(records_by_path.get(record["path"]) != record for record in previous["files"]):
            raise ValueError("Refusing to remove or change a previous source record")
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    temporary = ROOT / "manifest.json.tmp"
    save_new_or_identical(temporary, manifest_bytes)
    temporary.replace(manifest_path)
    print(json.dumps({"commit": COMMIT, "verified_files": len(records),
                      "bytes": sum(int(record["size"]) for record in records)}))


if __name__ == "__main__":
    main()
