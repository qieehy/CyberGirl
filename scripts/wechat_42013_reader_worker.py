"""Owned read-only 4.2.0.13 source; never any UI or model capability."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from scripts.wechat_42013_profile import activate
    activate()
    from wechat_reader_source import ReaderSource

    from scripts.wechat_reader_worker import serve
    serve(ReaderSource)
except BaseException:
    raise SystemExit(2) from None
