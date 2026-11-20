"""Owned .13 workbench child; explicit final-body BMP mode only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.wechat_42013_profile import activate  # noqa: E402

activate()
if "--input-method" not in sys.argv or sys.argv[sys.argv.index("--input-method") + 1] != "workbench_background_bmp":
    raise SystemExit(2)
from scripts.wechat_standalone_worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
