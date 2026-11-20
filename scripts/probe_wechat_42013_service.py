"""Explicit 4.2.0.13 SocialService acceptance; same original ledger and permits."""
from pathlib import Path

from scripts.wechat_42013_profile import activate, compatible_builds

activate()
from scripts import probe_wechat_standalone as preparation  # noqa: E402

preparation.WORKER_SCRIPT = Path(__file__).with_name("wechat_42013_worker.py")
from scripts import probe_wechat_background_service as entry  # noqa: E402

entry.STANDALONE_WORKER_SCRIPT = preparation.WORKER_SCRIPT
entry.READER_WORKER_SCRIPT = Path(__file__).with_name("wechat_42013_reader_worker.py")
entry.COMPATIBLE_BUILDS = compatible_builds
if __name__ == "__main__":
    raise SystemExit(entry.main())
