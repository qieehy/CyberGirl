"""Explicit filehelper fixed-marker backend; no default service registration.

All UI effects use bound element methods. Native account/page checks precede
each effect; the UI transport rechecks current control identity and draft.
The current build's focus-only button dispatch is rejected before effects;
there is no verified alternative submission path yet.
"""

import hashlib
import re
import time

from ...social.models import SocialError
from .click_geometry import geometry_report, parse_geometry
from .contracts import require_send_target
from .qt_connections import inspect_clicked_connection
from .qt_memory import inspect_button_dispatch, runtime_accessible_id, verify_current_filehelper
from .sender import InputObservation


class NativeSendBackend:
    def __init__(self, expected_window, expected_account_fingerprint, opener, *, post_submit_timeout=0.6):
        if not 0 < post_submit_timeout <= 1:
            raise ValueError("post-submit observation must be bounded to one second")
        self.post_submit_timeout = post_submit_timeout
        self.window, self.fingerprint, self.opener = expected_window, expected_account_fingerprint, opener
        self.binding = self.action = self.memory = self.ui = self.account_reader = None
        self.initial = None
        self.focus_used = self.input_used = self.submit_used = False

    def configure(self, binding, action):
        if self.binding is not None:
            raise SocialError("native_backend_already_configured")
        require_send_target(binding, action, observed_account=binding.account.account_id,
                            observed_username=binding.username, observed_window=self.window)
        if (binding.account.window != self.window or binding.account.self_id != binding.account.account_id
                or hashlib.sha256(binding.account.account_id.encode()).hexdigest() != self.fingerprint
                or not re.fullmatch(r"W2B-[A-Za-z0-9-]{1,64}", action.text)):
            raise SocialError("native_backend_binding_denied")
        self.binding, self.action = binding, action

    def inspect(self):
        if self.binding is None:
            raise SocialError("native_backend_not_initialized")
        started = time.monotonic()
        if self.memory is None:
            self.memory, self.account_reader, self.ui = self.opener(self.binding)
        opened = time.monotonic()
        data = self.ui.observe()
        observed = time.monotonic()
        if (type(data) is not dict or set(data) != {"runtime_ids", "input_token", "focused", "empty", "draft_sha256", "send_enabled"}
                or any(type(data.get(k)) is not bool for k in ("focused", "empty", "send_enabled"))
                or not isinstance(data["input_token"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["input_token"])
                or not isinstance(data["draft_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["draft_sha256"])
                or type(data["runtime_ids"]) is not dict
                or set(data["runtime_ids"]) != {"selected_session", "chat_input", "page", "button"}):
            raise SocialError("native_surface_shape_invalid")
        ids = data["runtime_ids"]
        for runtime in ids.values():
            runtime_accessible_id(runtime)
        verify_current_filehelper(self.memory, ids["selected_session"], ids["chat_input"],
                                  account_reader=self.account_reader, expected_account_fingerprint=self.fingerprint)
        self.send_enabled = data["send_enabled"]
        self.runtime_ids = ids  # Transient worker memory only, never included in reports.
        self.timings = dict(open_ms=round((opened - started) * 1000), ui_ms=round((observed - opened) * 1000),
                            native_ms=round((time.monotonic() - observed) * 1000))
        return InputObservation(self.binding.account.account_id, "filehelper", self.window, "native_username",
                                data["input_token"], data["focused"], data["empty"], data["draft_sha256"])

    def prepare_input_focus(self):
        if self.focus_used or self.input_used:
            raise SocialError("native_focus_already_used")
        self._require_submit_path()
        current = self.inspect()
        if not current.empty or current.draft_sha256 != hashlib.sha256(b"").hexdigest():
            raise SocialError("existing_draft_preserved")
        self.focus_used = True
        self.ui.focus_once(current.input_token)
        self.inspect().verify(self.binding, self.action)

    def draft_diagnostics(self, *, expected_token=None):
        first = self.inspect()  # Native account/page proof; no focus or empty-draft requirement.
        if expected_token is not None and first.input_token != expected_token:
            raise SocialError("native_input_changed_after_submission")
        data = self.ui.draft_patterns(first.input_token)
        if (type(data) is not dict or set(data) != {"input_token", "comparison", "send_enabled"}
                or data["input_token"] != first.input_token or type(data["comparison"]) is not dict):
            raise SocialError("draft_diagnostic_shape_invalid")
        comparison = data["comparison"]
        if set(comparison) != {"value_sha256", "value_length", "value_empty", "text_status", "text_sha256",
                               "text_length", "text_empty", "exact_equal", "trailing_linebreaks_only_difference"}:
            raise SocialError("draft_diagnostic_shape_invalid")
        if (type(data["send_enabled"]) is not bool
                or any(type(comparison[k]) is not bool for k in ("value_empty", "exact_equal", "trailing_linebreaks_only_difference"))
                or type(comparison["value_length"]) is not int or not 0 <= comparison["value_length"] <= 2000
                or comparison["value_empty"] != (comparison["value_length"] == 0)
                or not isinstance(comparison["value_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", comparison["value_sha256"])
                or comparison["text_status"] not in {"read", "too_long", "unsupported"}):
            raise SocialError("draft_diagnostic_shape_invalid")
        if comparison["text_status"] == "read":
            if (not isinstance(comparison["text_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", comparison["text_sha256"])
                    or type(comparison["text_length"]) is not int or not 0 <= comparison["text_length"] <= 2000
                    or type(comparison["text_empty"]) is not bool
                    or comparison["text_empty"] != (comparison["text_length"] == 0)):
                raise SocialError("draft_diagnostic_shape_invalid")
        elif (any(comparison[k] is not None for k in ("text_sha256", "text_length", "text_empty"))
              or comparison["exact_equal"] or comparison["trailing_linebreaks_only_difference"]):
            raise SocialError("draft_diagnostic_shape_invalid")
        current = self.inspect()
        if (current.input_token != first.input_token or current.draft_sha256 != first.draft_sha256
                or comparison["value_sha256"] != first.draft_sha256):
            raise SocialError("draft_changed_during_diagnostic")
        return dict(comparison=comparison, send_enabled=data["send_enabled"], native_identity_verified=True)

    def button_diagnostics(self):
        first = self.inspect()
        result = inspect_button_dispatch(self.memory, self.runtime_ids["button"])
        current = self.inspect()
        if current.input_token != first.input_token or current.draft_sha256 != first.draft_sha256:
            raise SocialError("surface_changed_during_button_diagnostic")
        return result

    def geometry_diagnostics(self):
        first = self.inspect()
        snapshot = parse_geometry(self.ui.click_geometry(first.input_token), first)
        current = self.inspect()
        if (current.input_token != first.input_token or current.draft_sha256 != first.draft_sha256
                or current.empty != first.empty):
            raise SocialError("surface_changed_during_geometry_diagnostic")
        return dict(geometry_report(snapshot), native_identity_verified=True, input_preserved=True)

    def connection_diagnostics(self):
        first = self.inspect()
        result = inspect_clicked_connection(self.memory, self.runtime_ids["button"], self.runtime_ids["chat_input"])
        current = self.inspect()
        if (current.input_token != first.input_token or current.draft_sha256 != first.draft_sha256
                or current.empty != first.empty):
            raise SocialError("surface_changed_during_connection_diagnostic")
        return dict(result, native_identity_verified=True, input_preserved=True, input_empty=first.empty,
                    draft_sha256=first.draft_sha256)

    def _require_submit_path(self):
        dispatch = self.button_diagnostics()
        action = next((m for m in dispatch["methods"] if m["offset"] == 32), None)
        if dispatch.get("interface_cast_rva") == 0x9EA840 and action and action["function_rva"] == 0x9EA590:
            raise SocialError("native_button_press_not_implemented")
        # No alternative submission path has been verified for this build.
        raise SocialError("native_button_submission_path_unverified")

    def input_text(self, text, token):
        if self.input_used or text != self.action.text:
            raise SocialError("native_input_already_used_or_changed")
        self._require_submit_path()
        current = self.inspect()
        current.verify(self.binding, self.action)
        if current.input_token != token:
            raise SocialError("native_input_control_changed")
        self.initial = current
        self.input_used = True
        self.ui.input_once(token, text)

    def submit_once(self, token):
        if not self.input_used or self.submit_used:
            raise SocialError("native_submit_order_denied")
        self._require_submit_path()
        current = self.inspect()
        current.verify(self.binding, self.action, initial=self.initial)
        if token != current.input_token or not self.send_enabled:
            raise SocialError("native_submit_control_changed_or_disabled")
        self.submit_used = True
        self.ui.submit_once(token)
        # Invoke returning only proves that a request was accepted. Observe the
        # same editor before the parent reaps this worker; never invoke again.
        deadline = time.monotonic() + self.post_submit_timeout
        while True:
            observation = self.draft_diagnostics(expected_token=token)
            comparison = observation["comparison"]
            if (comparison["value_empty"] and comparison["text_status"] == "read"
                    and comparison["text_empty"] and comparison["exact_equal"]):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SocialError("submission_called_but_draft_not_verified_empty")
            time.sleep(min(0.05, remaining))
