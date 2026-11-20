"""Explicit 4.2.0.13 preparation; never an automatic foreground fallback."""
from scripts.wechat_42013_profile import activate

activate()
from pathlib import Path  # noqa: E402

from scripts import probe_wechat_standalone as entry  # noqa: E402

entry.WORKER_SCRIPT = Path(__file__).with_name("wechat_42013_worker.py")
if __name__ == "__main__":
    raise SystemExit(entry.main())
