"""B18a offline audit. Never import the upstream Windows module.

Only selected function definitions execute, with all OS/UI effects replaced by fakes.
This is an effect-contract experiment, not a WeChat delivery experiment.
"""

import ast
import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "research/wechat_bridge/2026-11-07/upstream-send-review/whale_girl"
FUNCTIONS = {
    "send", "_noop", "_is_blank", "_composer_text", "_expected_text",
    "encode_gbk", "norm_chat_name", "_tokens", "name_score", "_chat_is_target",
}


def verified_tree():
    for name in ("manifest.json", "dependency-manifest.json"):
        path = SNAPSHOT / name
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        for item in manifest["files"]:
            data = (SNAPSHOT / (item["path"] + ".source.txt")).read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("snapshot_digest_mismatch:" + item["path"])
    return ast.parse((SNAPSHOT / "bridge/bg_sender.py.source.txt").read_text(encoding="utf-8"))


def experiment(scenario):
    """Run unchanged upstream send body against a deterministic, isolated fake UI."""
    allowed = {
        "success", "mismatch", "switch_during_input", "draft", "unreadable",
        "input_exception", "click_exception", "verify_unreadable",
    }
    if scenario not in allowed:
        raise ValueError("unknown_scenario")
    tree = verified_tree()
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    if {n.name for n in selected} != FUNCTIONS:
        raise ValueError("upstream_function_set_changed")
    # No imports, module initializers, ctypes, HWND, DLLs or real clocks in this namespace.
    events = []
    state = {"value": "", "chat": "filehelper", "clicked": False}
    if scenario == "draft":
        state["value"] = "existing draft"
    if scenario == "unreadable":
        state["value"] = None

    def read_ui(_hwnd):
        value = state["value"]
        if scenario == "verify_unreadable" and state["clicked"]:
            value = None
        return {"nodes": list(range(20)), "edit": "fake-edit", "send_xy": (10, 10),
                "value": value, "chat": state["chat"]}

    class Sender:
        def __init__(self, _hwnd):
            pass

        def type_text(self, text, **kwargs):
            events.append("input:" + kwargs["mode"])
            if scenario == "input_exception":
                raise RuntimeError("injected_after_input_intent")
            state["value"] = "corrupt" if scenario == "mismatch" else text
            if scenario == "switch_during_input":
                state["chat"] = "other-chat"
            return True, []

        def end_input(self):
            events.append("end_input")

        def click_screen(self, *_xy):
            events.append("submit")
            if scenario == "click_exception":
                raise RuntimeError("injected_after_submit_intent")
            state["clicked"] = True
            state["value"] = ""

    def clear(*_args):
        events.append("clear")
        state["value"] = ""
        return True

    clock = [0.0]

    def sleep(seconds):
        clock[0] += max(seconds, 0.001)

    def remember(*_args):
        events.append("remember_mode")

    namespace = {
        "re": re, "user32": SimpleNamespace(IsIconic=lambda _: False),
        "WeChatSender": Sender, "_WriteGuard": nullcontext,
        "_ensure_tree": lambda hwnd, **_: read_ui(hwnd), "read_ui": read_ui,
        "MIN_NODES": 20, "_CONFIGURED_MODES": {}, "_DETECTED_MODES": {},
        "_MODE_LOGGED": set(), "_DETECT_DIRTY": set(),
        "TIMING": {"sendEnabled": 0.35, "afterClick": 0.4, "verifyPoll": 0.2},
        "time": SimpleNamespace(sleep=sleep, time=lambda: clock[0]),
        "_remember_char_mode": remember, "_clear_composer": clear,
        "UipiDenied": type("UipiDenied", (RuntimeError,), {}),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<offline-upstream>", "exec"), namespace)
    # chat=None is the proposed no-switch configuration, and therefore the issue under test.
    ok, reason = namespace["send"](
        "B18-offline-中文" if scenario == "mismatch" else "B18-offline-中文🙂",
        chat=None, hwnd=1, unhide=False,
        char_mode="utf16", strip_unencodable=False,
    )
    return {"scenario": scenario, "upstream_ok": ok, "reason": reason, "events": events,
            "final_target_matches": state["chat"] == "filehelper",
            "delivery_evidence": False, "real_effects": False}


def audit():
    tree = verified_tree()
    imports = []
    sources = list((SNAPSHOT / "bridge").glob("*.source.txt"))
    sources += list((SNAPSHOT / "vendor").rglob("*.py.source.txt"))
    for source in sources:
        parsed = ast.parse(source.read_text(encoding="utf-8-sig"))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imports.extend({"file": source.relative_to(SNAPSHOT).as_posix(), "line": node.lineno, "module": a.name}
                               for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append({"file": source.relative_to(SNAPSHOT).as_posix(), "line": node.lineno, "module": node.module, "level": node.level})
    return {
        "stage": "B18a", "live_admitted": False, "source_verified": True,
        "license": "AGPL-3.0-only (this snapshot; older MIT snapshot is separate)",
        "definitions": {n.name: n.lineno for n in tree.body
                        if isinstance(n, (ast.FunctionDef, ast.ClassDef))},
        "imports": imports,
        "experiments": [experiment(s) for s in (
            "success", "mismatch", "switch_during_input", "draft", "unreadable",
            "input_exception", "click_exception", "verify_unreadable",
        )],
        "blockers": [
            "original retries and clears despite explicit utf16",
            "chat=None skips target checks; display-name checks are insufficient",
            "named mutex acquisition failure permits send; local lock has no deadline",
            "gate pulse does not restore owned changes; shutdown only restores SPI",
            "transitive dependencies not yet fully pinned",
            "no admitted constrained live backend or persistent permit wiring",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
