"""Bounded B18 background-control pipes. Parent owns all original-ledger permits."""

import json
import re

from ...social.models import SocialError
from .send_process import MAX_FRAME, JsonSendWorker, unique_object

STAGES = frozenset({"PSEUDO_FOCUS_INTENT", "CHAR_INTENT", "MOVE_INTENT", "DOWN_INTENT", "RETURN_DOWN_INTENT", "RETURN_UP_INTENT",
                    "UP_INTENT", "PSEUDO_FOCUS_END_INTENT", "VALUE_INPUT_INTENT"})


class BackgroundWorker(JsonSendWorker):
    def __init__(self, command, *, record=None):
        super().__init__(command)
        self.background_record = record
        self.background_used = False

    async def _rpc(self, command, **payload):
        if command not in {"init", "prepare", "run_background"}:
            raise SocialError("background_worker_command_denied")
        if command == "run_background":
            if self.background_used or self.background_record is None:
                raise SocialError("background_worker_permit_missing_or_used")
            self.background_used = True
        self.sequence += 1
        request = dict(version=1, operation_id=self.operation_id, sequence=self.sequence, command=command, **payload)
        data = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_FRAME:
            raise SocialError("background_worker_frame_bound")
        self.process.stdin.write(data)
        await self.process.stdin.drain()
        ordinal = 0
        while True:
            raw = await self.process.stdout.readuntil(b"\n")
            if len(raw) > MAX_FRAME:
                raise SocialError("background_worker_frame_bound")
            response = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            if (type(response) is not dict or response.get("operation_id") != self.operation_id
                    or type(response.get("sequence")) is not int or response["sequence"] != self.sequence):
                raise SocialError("background_worker_response_mismatch")
            if "background_intent" not in response:
                break
            intent = response["background_intent"]
            if (command != "run_background" or self.background_record is None or ordinal >= 102
                    or set(response) != {"operation_id", "sequence", "background_intent"}
                    or type(intent) is not dict or set(intent) != {"stage", "ordinal"}
                    or type(intent["stage"]) is not str or intent["stage"] not in STAGES
                    or type(intent["ordinal"]) is not int or intent["ordinal"] != ordinal + 1):
                raise SocialError("background_worker_intent_invalid")
            ordinal += 1
            accepted = False
            try:
                accepted = self.background_record(intent["stage"]) is True
            except Exception:
                pass  # Fail closed, but let the original-window cleanup request be considered.
            ack = dict(operation_id=self.operation_id, sequence=self.sequence,
                       background_ack={**intent, "accepted": accepted})
            self.process.stdin.write(json.dumps(ack, separators=(",", ":")).encode() + b"\n")
            await self.process.stdin.drain()
        if set(response) != {"operation_id", "sequence", "result"} or type(response["result"]) is not dict:
            raise SocialError("background_worker_terminal_invalid")
        result = response["result"]
        if "rejected_reason" in result:
            reason = result["rejected_reason"]
            if type(reason) is not str or not re.fullmatch(r"[a-z0-9_]{1,128}", reason):
                raise SocialError("background_worker_rejection_invalid")
            raise SocialError(reason)
        return result


class BackgroundIntentClient:
    def __init__(self, source, destination, operation_id, sequence):
        self.source, self.destination = source, destination
        self.operation_id, self.sequence, self.ordinal = operation_id, sequence, 0

    def record(self, stage):
        if stage not in STAGES or self.ordinal >= 102:
            raise SocialError("background_client_stage_denied")
        self.ordinal += 1
        intent = dict(stage=stage, ordinal=self.ordinal)
        self.destination.write(json.dumps(dict(operation_id=self.operation_id, sequence=self.sequence,
            background_intent=intent), separators=(",", ":")).encode() + b"\n")
        self.destination.flush()
        raw = self.source.readline(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            raise SocialError("background_client_ack_invalid")
        ack = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if (type(ack) is not dict or set(ack) != {"operation_id", "sequence", "background_ack"}
                or ack["operation_id"] != self.operation_id or type(ack["sequence"]) is not int
                or ack["sequence"] != self.sequence or type(ack["background_ack"]) is not dict
                or set(ack["background_ack"]) != {"stage", "ordinal", "accepted"}
                or ack["background_ack"]["stage"] != stage
                or type(ack["background_ack"]["ordinal"]) is not int
                or ack["background_ack"]["ordinal"] != self.ordinal
                or type(ack["background_ack"]["accepted"]) is not bool):
            raise SocialError("background_client_ack_invalid")
        return ack["background_ack"]["accepted"]
