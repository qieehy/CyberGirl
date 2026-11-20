"""Synthetic JSON child, intentionally incapable of using the Windows UI."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1] == "parent-exit":
    from wechat_agent.bridges.wechat.send_process import KillJob

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=subprocess.CREATE_NO_WINDOW)
    job = KillJob(child.pid)
    Path(sys.argv[2]).write_text(str(child.pid))
    os._exit(0)

mode, initialized, touched = sys.argv[1], None, False
if mode in {"normal", "late-submit"}:
    from test_wechat_bridge import BINDING, WINDOW

    from wechat_agent.bridges.wechat.send_worker import serve
    from wechat_agent.bridges.wechat.sender import InputObservation

    class Backend:
        text = ""

        def prepare_input_focus(self):
            pass

        def inspect(self):
            return InputObservation(BINDING.account.account_id, "filehelper", WINDOW, "native_username",
                "synthetic-input", True, not self.text, hashlib.sha256(self.text.encode()).hexdigest())

        def input_text(self, text, token):
            self.text = text

        def submit_once(self, token):
            if mode == "late-submit":
                time.sleep(0.3)
                Path(sys.argv[2]).write_text("synthetic late submit")

    serve(Backend(), sys.stdin.buffer, sys.stdout.buffer)
    raise SystemExit(0)

for line in sys.stdin.buffer:
    message = json.loads(line)
    if mode == "hang":
        time.sleep(30)
    if mode == "oversized":
        print("x" * 20000, flush=True)
        continue
    command = message["command"]
    result = {"accepted": True}
    if command == "init":
        initialized = message
    elif command == "inspect":
        binding = initialized["binding"]
        result = {"observation": {"account_id": binding["account"]["account_id"], "username": binding["username"],
            "window": binding["account"]["window"], "identity_method": "native_username", "input_token": "synthetic-input",
            "focused": True, "empty": not touched,
            "draft_sha256": hashlib.sha256((initialized["action"]["text"] if touched else "").encode()).hexdigest()}}
    elif command == "input_once":
        touched = True
    response = {"operation_id": "wrong" if mode == "wrong-operation" else message["operation_id"],
                "sequence": message["sequence"], "result": result}
    data = json.dumps(response)
    if mode == "duplicate":
        data = data[:-1] + ',"sequence":1}'
    print(data, flush=True)
