"""Worker-side one-use protocol. No native backend or executable is enabled.

An eventual backend must inspect the canonical native username, target a bound
input control and use one verified submission method. It must not navigate,
clear drafts, guess names, retry, or fall back to global keyboard shortcuts.
"""

import json
from dataclasses import asdict

from ...context import ConversationScope
from ...social.models import OutgoingAction, SocialError, identifier
from .contracts import AccountBinding, ConversationBinding, WindowBinding, require_send_target
from .directed_click import ClickResult
from .gesture_protocol import GestureIntentClient
from .send_process import MAX_FRAME, unique_object
from .sender import InputObservation


class WorkerSession:
    def __init__(self, backend):
        self.backend = backend
        self.binding = self.action = self.operation_id = self.initial = None
        self.sequence = 0
        self.input_used = self.submit_used = self.ready_to_submit = False
        self.focus_used = False
        self.failed = False

    def _observe(self, *, after_input=False):
        observation = self.backend.inspect()
        if not isinstance(observation, InputObservation):
            raise SocialError("invalid_native_observation")
        observation.verify(self.binding, self.action, initial=self.initial if after_input else None)
        if self.initial is not None and observation.input_token != self.initial.input_token:
            raise SocialError("native_input_control_changed")
        return observation

    def handle(self, request):
        try:
            return self._handle(request)
        except BaseException:
            self.failed = True  # A failed native action/observation is terminal, never replayed.
            raise

    def _handle(self, request):
        if (self.failed or type(request) is not dict or type(request.get("version")) is not int
                or request["version"] != 1 or type(request.get("sequence")) is not int
                or request["sequence"] != self.sequence + 1):
            raise SocialError("invalid_worker_request")
        operation_id = request.get("operation_id")
        identifier(operation_id)
        command = request.get("command")
        keys = {"version", "operation_id", "sequence", "command"}
        if self.binding is None:
            if command != "init" or set(request) != keys | {"binding", "action"}:
                raise SocialError("worker_initialization_required")
            data = dict(request["binding"])
            account = dict(data["account"])
            account["window"] = WindowBinding(**account["window"])
            data["account"] = AccountBinding(**account)
            self.binding = ConversationBinding(**data)
            data = dict(request["action"])
            data["scope"] = ConversationScope(**data["scope"])
            self.action = OutgoingAction(**data)
            require_send_target(self.binding, self.action, observed_account=self.binding.account.account_id,
                                observed_username=self.binding.username, observed_window=self.binding.account.window)
            self.operation_id = operation_id
            configure = getattr(self.backend, "configure", None)
            if configure:
                configure(self.binding, self.action)
            result = {"accepted": True}
        else:
            if operation_id != self.operation_id or set(request) != keys or self.submit_used:
                raise SocialError("invalid_worker_operation")
            if command == "readiness_diagnostics" and self.initial is None and not self.input_used:
                result = {"readiness_diagnostics": self.backend.readiness_diagnostics()}
            elif command == "connection_diagnostics" and self.initial is None and not self.input_used and not self.focus_used:
                result = {"connection_diagnostics": self.backend.connection_diagnostics()}
            elif command == "geometry_diagnostics" and self.initial is None and not self.input_used and not self.focus_used:
                result = {"geometry_diagnostics": self.backend.geometry_diagnostics()}
            elif command == "button_diagnostics" and self.initial is None and not self.input_used and not self.focus_used:
                result = {"button_diagnostics": self.backend.button_diagnostics()}
            elif command == "draft_diagnostics" and self.initial is None and not self.input_used and not self.focus_used:
                result = {"draft_diagnostics": self.backend.draft_diagnostics()}
            elif command == "capabilities" and self.initial is None and not self.input_used:
                observation = self.backend.inspect()
                require_send_target(self.binding, self.action, observed_account=observation.account_id,
                                    observed_username=observation.username, observed_window=observation.window)
                result = {"observation": asdict(observation)}  # No focus requirement, grants no input state.
                if hasattr(self.backend, "timings"):
                    result["timings"] = self.backend.timings
            elif command == "focus_once" and self.initial is None and not self.focus_used:
                self.focus_used = True
                self.backend.prepare_input_focus()
                result = {"accepted": True}
            elif command == "inspect":
                observation = self._observe(after_input=self.input_used)
                if self.input_used:
                    self.ready_to_submit = True
                else:
                    self.initial = observation
                result = {"observation": asdict(observation)}
            elif command == "input_once" and self.initial is not None and not self.input_used:
                self._observe()  # Close the parent-observation/IPC gap before touching input.
                self.input_used = True
                self.backend.input_text(self.action.text, self.initial.input_token)
                result = {"accepted": True}
            elif command == "submit_once" and self.ready_to_submit:
                self._observe(after_input=True)  # Recheck again in the native process.
                self.submit_used = True
                gesture = self.backend.submit_once(self.initial.input_token)
                if gesture is not None and not isinstance(gesture, ClickResult):
                    raise SocialError("invalid_gesture_result")
                result = {"gesture_result": asdict(gesture)} if gesture is not None else {"accepted": True}
            else:
                raise SocialError("worker_permit_order_denied")
        self.sequence = request["sequence"]
        return {"operation_id": self.operation_id, "sequence": self.sequence, "result": result}


def serve(backend, source, destination):
    """Binary bounded pipes; caller owns process exit and never prints exceptions."""
    session = WorkerSession(backend)
    intents = GestureIntentClient(source, destination)
    attach = getattr(backend, "set_gesture_recorder", None)
    if attach:
        attach(intents.record)
    while True:
        line = source.readline(MAX_FRAME + 1)
        if not line:
            return
        if len(line) > MAX_FRAME or not line.endswith(b"\n"):
            raise SocialError("worker_request_too_large")
        terminal = False
        request = json.loads(line.decode("utf-8"), object_pairs_hook=unique_object)
        intents.context(request)
        try:
            response = session.handle(request)
        except SocialError as exc:
            terminal = True
            response = dict(operation_id=session.operation_id, sequence=request.get("sequence"), result={"rejected_reason": exc.code})
        output = json.dumps(response, separators=(",", ":")).encode() + b"\n"
        if len(output) > MAX_FRAME:
            raise SocialError("worker_response_too_large")
        destination.write(output)
        destination.flush()
        if terminal:
            return
