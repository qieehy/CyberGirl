"""Opt-in upstream complete-sequence trial; default routes remain unchanged."""

import time
from dataclasses import replace

from ...social.models import SocialError
from .directed_click import pack_client_point
from .directed_native import NativeDirectedTrial
from .posted_click import PostedObservation
from .posted_native import NativePostedTrial
from .posted_sequence import PostedSequence
from .qt_memory import verify_current_filehelper

SEQUENCE_MARKER = "W2B-20261107-sequence-once"


class NativeSequenceTrial(NativePostedTrial):
    trial_marker = SEQUENCE_MARKER

    def __init__(self, *args, clock=time.perf_counter, **kwargs):
        super().__init__(*args, clock=clock, **kwargs)

    def prepare_input_focus(self):
        # Same bound-element preparation already approved by the operator;
        # keeping the outer lease alive avoids rebuilding the provider between
        # focus preparation and the reviewed trial's readiness observation.
        NativeDirectedTrial.prepare_input_focus(self)

    def _native_guard(self, message):
        # No UIA roundtrip within the gesture. The emitter separately binds the
        # original HWND/process/thread/class/point and foreground/hardware twice.
        verify_current_filehelper(self.memory, self.runtime_ids["selected_session"], self.runtime_ids["chat_input"],
            account_reader=self.account_reader, expected_account_fingerprint=self.fingerprint)

    def readiness_diagnostics(self):
        return dict(super().readiness_diagnostics(), gesture="upstream_complete_sequence",
                    pressed_polling_during_gesture=False, intents_precommitted=True)

    def submit_once(self, token):
        if not self.input_used or self.submit_used or self.initial is None or self.gesture_record is None:
            raise SocialError("sequence_submit_order_denied")
        self.submit_used = True
        if token != self.initial.input_token:
            raise SocialError("sequence_editor_changed")

        def observe():
            snapshot, connection = self._snapshot()
            self.gesture_before = snapshot
            self.messages.thread = snapshot.receiver_thread
            self.messages.point = pack_client_point(snapshot.client_point)
            self.messages.verify_effect = self._native_guard
            return PostedObservation(snapshot, connection["pressed_bit_set"])

        result = PostedSequence(self.binding, self.action, self.initial, observe=observe,
            post=lambda *args: self.messages.post(*args), cancel=lambda *args: self.messages.cancel(*args),
            record=self.gesture_record, clock=self.clock, sleep=self.sleep).perform_once()
        if result.state != "REQUESTED":
            return result
        try:
            self._wait_completion(token)
            return replace(result, reason="sequence_requested_editor_empty_and_button_released")
        except Exception as exc:
            return replace(result, state="UNKNOWN", reason=exc.code if isinstance(exc, SocialError)
                           else "sequence_completion_observation_failed")
