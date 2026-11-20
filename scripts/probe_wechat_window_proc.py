"""Original bound HWND procedure pointers and code hashes; no UI activation.

No accessibility lease, callback invocation, chat data, or window messages.
Opaque/out-of-module procedure handles are rejected, never dereferenced.
"""

import argparse
import ctypes as C
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.window_proc import inspect_window_procedures
from wechat_agent.social.models import SocialError

if __package__:
    from .verify_wechat_accessibility import DLL, NativeMemory
    from .wechat_pinned_image import PinnedImage
else:
    from verify_wechat_accessibility import DLL, NativeMemory
    from wechat_pinned_image import PinnedImage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--kind", choices=("both", "window", "class"), default="both",
                        help="Observe only the explicitly selected metadata; class alone proves no per-window procedure")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = dict(status="started", process_writes=0, accessibility_leases=0, window_messages_sent=0,
                  input_actions=0, submit_actions=0, focus_actions=0, sender_ledger_writes=0)
    with args.output.open("x", encoding="utf-8") as output:
        memory = NativeMemory(args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime)
        try:
            memory.preflight()
            image = PinnedImage(DLL.read_bytes())
            for name in ("GetWindowLongPtrW", "GetClassLongPtrW"):
                api = getattr(memory.u, name)
                api.argtypes, api.restype = [C.c_void_p, C.c_int], C.c_size_t

            def get_procedure(kind):
                memory.validate()
                api, index = ((memory.u.GetWindowLongPtrW, -4) if kind == "window"
                              else (memory.u.GetClassLongPtrW, -24))
                C.set_last_error(0)
                pointer = api(args.hwnd, index)
                if not pointer:
                    failure = SocialError("window_proc_query_failed")
                    failure.win32_error = C.get_last_error()
                    failure.procedure_kind = kind
                    raise failure
                return pointer

            kinds = ("window", "class") if args.kind == "both" else (args.kind,)
            observed = inspect_window_procedures(memory, lambda rva, size: image.at(rva, size, executable=True),
                                                  get_procedure, kinds=kinds)
            result.update(status="window_procedures_observed" if args.kind == "both" else args.kind + "_procedure_observed", **observed)
        except Exception as exc:
            result.update(status="not_verified", error_type=type(exc).__name__)
            if isinstance(exc, SocialError):
                result["reason"] = exc.code
                if exc.code == "window_proc_query_failed":
                    result.update(win32_error=exc.win32_error, procedure_kind=exc.procedure_kind)
        finally:
            memory.close()
            paths = ["scripts/probe_wechat_window_proc.py", "scripts/wechat_pinned_image.py",
                     "backend/src/wechat_agent/bridges/wechat/window_proc.py", "scripts/verify_wechat_accessibility.py"]
            root = Path(__file__).resolve().parent.parent
            result.update(recorded_at=datetime.now(UTC).isoformat(),
                          implementation_sha256={p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths})
            json.dump(result, output, indent=2)
            output.write("\n")
    print(json.dumps(result))
    return 0 if result["status"] in {"window_procedures_observed", "window_procedure_observed", "class_procedure_observed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
