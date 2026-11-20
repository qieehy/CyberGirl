"""Workspace commands; checks use synthetic adapters and never start live chat."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "tests/test_web_assets.py", "tests/test_console.py", "tests/test_workbench.py",
    "tests/test_managed_companion.py", "tests/test_private_auto_reply.py",
    "tests/test_private_model_drafts.py", "tests/test_wechat_private_media.py",
    "tests/test_private_background_send.py", "tests/test_private_foreground_send.py",
]


def run(command):
    print("Running: " + " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "test", "build", "frontend"))
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        parser.error("Node.js >=22 and npm are required")
    if args.command == "frontend":
        run([npm, "--prefix", "frontend", "run", "dev"])
    elif args.command == "build":
        run([npm, "--prefix", "frontend", "run", "build"])
    elif args.command == "check":
        run([npm, "--prefix", "frontend", "run", "check"])
        run([sys.executable, "-m", "ruff", "check", "backend/src/wechat_agent/web", "tests/test_web_assets.py"])
    else:
        # Static-resource tests require a current build, not stale checked-in files.
        run([npm, "--prefix", "frontend", "run", "build"])
        run([npm, "--prefix", "frontend", "run", "test"])
        run([sys.executable, "-m", "pytest", *TESTS, "-q"])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
