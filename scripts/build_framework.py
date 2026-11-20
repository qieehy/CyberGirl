"""Build a fingerprinted dependency wheel without writing to the framework checkout.

Only package Python sources and public project metadata enter the build staging area.
The original package discovery misses observability and prompts; the manifest records
this packaging-only overlay. Framework source code itself is not patched.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("llm", "memory", "tools", "runtime", "config", "errors", "rag", "observability", "prompts")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    original = (source / "pyproject.toml").read_text(encoding="utf-8")
    metadata = tomllib.loads(original)
    if metadata["project"]["name"] != "ai-agent" or metadata["project"]["version"] != "0.3.0":
        raise SystemExit("Unexpected framework identity; review the dependency before rebuilding")
    selected = [source / "pyproject.toml", source / "README.md"]
    selected.extend(p for p in [source / "LICENSE", source / "LICENSE.md"] if p.is_file())
    for package in PACKAGES:
        selected.extend(sorted((source / package).rglob("*.py")))
    content = {p.relative_to(source).as_posix(): p.read_bytes() for p in selected}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(content.items())}
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    existing = metadata["tool"]["setuptools"]["packages"]["find"]["include"]
    expected = '["llm*", "memory*", "tools*", "runtime*", "config*", "errors*", "rag*"]'
    if existing != ["llm*", "memory*", "tools*", "runtime*", "config*", "errors*", "rag*"] or original.count(expected) != 1:
        raise SystemExit("Framework packaging changed; review the overlay")
    patched = original.replace(expected, expected[:-1] + ', "observability*", "prompts*"]')
    wheels = ROOT / ".build" / "wheels"
    wheel = wheels / "ai_agent-0.3.0-py3-none-any.whl"
    manifest = {"source": str(source), "name": "ai-agent", "version": "0.3.0",
                "source_sha256": fingerprint, "files": hashes,
                "packaging_overlay": ["include observability*", "include prompts*"],
                "wheel": wheel.relative_to(ROOT).as_posix(),
                "build_tools": {"setuptools": "84.0.0", "wheel": "0.48.0"}}
    lock = ROOT / "framework.lock.json"
    previous = json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else None
    if previous is not None:
        if {k: v for k, v in previous.items() if k != "wheel_sha256"} != manifest:
            raise SystemExit("Framework fingerprint changed; review before building")
    elif wheel.exists():
        raise SystemExit("Existing framework wheel has no lock; review before replacing it")
    wheels.mkdir(parents=True, exist_ok=True)
    # Every build owns fresh staging and output directories. Rejection cannot
    # overwrite the last accepted wheel or pick up stale package sources.
    with tempfile.TemporaryDirectory(prefix="framework-candidate-", dir=ROOT / ".build") as directory:
        candidate_root = Path(directory).resolve()
        if not candidate_root.is_relative_to((ROOT / ".build").resolve()):
            raise SystemExit("Candidate directory escaped build root")
        staging = candidate_root / "source"
        for name, data in content.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (staging / "pyproject.toml").write_text(patched, encoding="utf-8")
        output = candidate_root / "wheels"
        output.mkdir()
        environment = dict(os.environ, SOURCE_DATE_EPOCH="1792972800",
                           TEMP=str(candidate_root), TMP=str(candidate_root))
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(staging), "--no-deps",
                        "--no-build-isolation", "--no-index", "--disable-pip-version-check",
                        "--no-cache-dir", "--wheel-dir", str(output)], check=True, env=environment)
        candidate = output / wheel.name
        manifest["wheel_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if previous is not None:
            if manifest != previous or json.loads(lock.read_text(encoding="utf-8")) != previous:
                raise SystemExit("Framework build differs from lock; previous wheel preserved")
        else:
            # Reserve the lock exclusively before publishing the first artifact.
            with lock.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        candidate.replace(wheel)
    print(f"Built {wheel.name}; framework source files: {len(hashes)}; {fingerprint}")


if __name__ == "__main__":
    main()
