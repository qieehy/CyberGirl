"""Owned 4.2.0.13 child; covered preparation or the two reviewed BMP controls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.wechat_42013_profile import activate  # noqa: E402

activate()
if "--input-method" not in sys.argv:
    raise SystemExit(2)
method = sys.argv[sys.argv.index("--input-method") + 1]
if method not in {"utf16_bmp_covered", "utf16_bmp_integrated"} or "--allow-control" in sys.argv and method != "utf16_bmp_integrated":
    raise SystemExit(2)
from scripts.wechat_standalone_worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
