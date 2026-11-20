"""Synthetic B18 protocol peer, no native imports or platform messages."""

import json
import sys

from wechat_agent.bridges.wechat.background_process import BackgroundIntentClient


def reply(request, result):
    print(json.dumps(dict(operation_id=request["operation_id"], sequence=request["sequence"], result=result)), flush=True)


init = json.loads(sys.stdin.buffer.readline())
reply(init, {"accepted": True})
prepare = json.loads(sys.stdin.buffer.readline())
reply(prepare, {"readiness": {"synthetic": True}})
run = json.loads(sys.stdin.buffer.readline())
client = BackgroundIntentClient(sys.stdin.buffer, sys.stdout.buffer, run["operation_id"], run["sequence"])
stages = ["PSEUDO_FOCUS_INTENT"] + ["CHAR_INTENT"] * (len(init["action"]["text"].encode("utf-16-le")) // 2)
stages += ["MOVE_INTENT", "DOWN_INTENT", "UP_INTENT", "PSEUDO_FOCUS_END_INTENT"]
for index, stage in enumerate(stages):
    if len(sys.argv) > 1 and sys.argv[1] == "hang" and index == 2:
        sys.stdin.buffer.readline()
        raise SystemExit(0)
    if not client.record(stage):
        if stage != "PSEUDO_FOCUS_END_INTENT":
            client.record("PSEUDO_FOCUS_END_INTENT")
        break
reply(run, {"control_result": {"state": "UNKNOWN", "delivery_verified": False}})
