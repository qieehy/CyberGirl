"""Worker-to-owner durable gesture permits over the existing private pipes."""

import json

from ...social.models import SocialError
from .send_process import MAX_FRAME, unique_object

INTENTS = frozenset({"CLICK_BEGIN_INTENT", "CLICK_MOVE_INTENT", "CLICK_DOWN_INTENT", "CLICK_UP_INTENT", "CLICK_CANCEL_INTENT"})
MAX_INTENTS = len(INTENTS)


class GestureIntentClient:
    def __init__(self, source, destination):
        self.source, self.destination = source, destination
        self.operation_id = self.sequence = self.command = None
        self.ordinal = 0

    def context(self, request):
        self.operation_id = request.get("operation_id")
        self.sequence, self.command = request.get("sequence"), request.get("command")
        self.ordinal = 0

    def record(self, stage):
        if self.command != "submit_once" or stage not in INTENTS or self.ordinal >= MAX_INTENTS:
            raise SocialError("gesture_intent_command_denied")
        self.ordinal += 1
        intent = {"stage": stage, "ordinal": self.ordinal}
        self.destination.write(json.dumps({"operation_id": self.operation_id,
            "sequence": self.sequence, "intent": intent}, separators=(",", ":")).encode() + b"\n")
        self.destination.flush()
        raw = self.source.readline(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            raise SocialError("gesture_intent_ack_invalid")
        ack = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if (type(ack) is not dict or set(ack) != {"operation_id", "sequence", "intent_ack"}
                or ack["operation_id"] != self.operation_id or type(ack["sequence"]) is not int
                or ack["sequence"] != self.sequence or type(ack["intent_ack"]) is not dict
                or set(ack["intent_ack"]) != {"stage", "ordinal", "accepted"}
                or type(ack["intent_ack"]["ordinal"]) is not int
                or ack["intent_ack"]["ordinal"] != self.ordinal or ack["intent_ack"]["stage"] != stage
                or type(ack["intent_ack"]["accepted"]) is not bool):
            raise SocialError("gesture_intent_ack_invalid")
        if not ack["intent_ack"]["accepted"]:
            raise SocialError("gesture_intent_not_committed")
