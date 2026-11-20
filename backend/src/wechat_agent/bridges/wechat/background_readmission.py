"""Bounded parent-owned revalidation after the tested WeChat process exits.

No UI, permits, new tables or delivery changes. A new native generation needs
fresh actual readiness and proof that other old input generations have exited.
The original fully confirmed covered trial remains a prerequisite.
"""
import json
import math
from dataclasses import dataclass, replace

from ...social.models import SocialError
from .background_completion import _require_generation_clean, require_background_admission
from .background_contract import COVERED_MARKER
from .contracts import WindowBinding

READY_FIELDS = {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                "standalone_identity_verified", "background_observer_active", "no_capture", "button_not_pressed",
                "class_entry_matches_pinned_code", "covered_by_other_app"}


@dataclass(frozen=True)
class BackgroundReadmission:
    binding: object
    source_binding: object
    observed_at: float
    compatible_builds: object = None

    @property
    def descriptor(self):
        return dict(source_generation=self.source_binding.generation, generation=self.binding.generation,
                    observed_at=self.observed_at, other_input_generations_exited=True,
                    build_transition_verified=self.compatible_builds is not None)

    def check(self, ledger, binding):
        now = ledger.now()
        if (binding != self.binding or type(self.observed_at) not in (int, float)
                or not math.isfinite(self.observed_at) or not self.observed_at <= now <= self.observed_at + 20):
            raise SocialError("background_readmission_expired_or_changed")
        old, current = self.source_binding.account.window, binding.account.window
        if ((old.exe_sha256, old.dll_sha256) != (current.exe_sha256, current.dll_sha256)
                and (self.compatible_builds is None or self.compatible_builds(old, current) is not True)):
            raise SocialError("background_readmission_source_incompatible")
        require_background_admission(ledger, self.source_binding)
        _require_generation_clean(ledger, binding, require_covered=False)
        return True


def revalidate_background(ledger, binding, readiness, *, retired_generations, compatible_builds=None):
    """Only the trusted native parent calls this after its fresh worker prepare."""
    if (type(readiness) is not dict or set(readiness) != READY_FIELDS
            or any(readiness[k] is not True for k in READY_FIELDS)):
        raise SocialError("background_readmission_readiness_incomplete")
    rows = ledger._db.execute("""SELECT s.window_binding,s.generation FROM wechat_send_operations s
        JOIN outbox o USING(action_id) WHERE s.scope=? AND o.text=?""", (binding.scope.key(), COVERED_MARKER)).fetchall()
    if len(rows) != 1:
        raise SocialError("background_readmission_source_missing")
    old = WindowBinding(**json.loads(rows[0]["window_binding"]))
    current = binding.account.window
    changed_build = (old.exe_sha256, old.dll_sha256) != (current.exe_sha256, current.dll_sha256)
    if ((old.pid, old.created_filetime) == (current.pid, current.created_filetime)
            or changed_build and (compatible_builds is None or compatible_builds(old, current) is not True)):
        raise SocialError("background_readmission_source_incompatible")
    source = replace(binding, account=replace(binding.account, window=old))
    if source.generation != rows[0]["generation"]:
        raise SocialError("background_readmission_source_incompatible")
    require_background_admission(ledger, source)
    _require_generation_clean(ledger, binding, require_covered=False)
    if retired_generations(ledger, current) is not True:
        raise SocialError("background_readmission_old_generation_live_or_unknown")
    context = BackgroundReadmission(binding, source, ledger.now(), compatible_builds if changed_build else None)
    context.check(ledger, binding)
    return context
