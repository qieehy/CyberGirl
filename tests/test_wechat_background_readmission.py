"""A restarted WeChat never inherits a live process admission or an old send permit."""
import hashlib
from dataclasses import replace

import pytest

from tests.test_wechat_background_completion import clean_result, finished
from tests.test_wechat_background_control import BINDING
from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_completion import (
    record_background_completion,
    require_background_admission,
)
from wechat_agent.bridges.wechat.background_contract import (
    COVERED_MARKER,
    INTEGRATED_MARKER,
    INTEGRATED_REQUEST,
)
from wechat_agent.bridges.wechat.background_readmission import READY_FIELDS, revalidate_background
from wechat_agent.bridges.wechat.contracts import Cursor, DeliveryEvidence
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


def seed(ledger, *, phone=True):
    operation, action = finished(ledger)
    record_background_completion(ledger, operation, clean_result(), reaped=True, restored=True, locks_released=True)
    sha = hashlib.sha256(COVERED_MARKER.encode()).hexdigest()
    store = BridgeStore(ledger)
    store.record_evidence(DeliveryEvidence("echo", action.action_id, BINDING.scope, BINDING.generation,
        "local_echo", operation, sha, "native-echo"))
    if phone:
        store.record_evidence(DeliveryEvidence("phone", action.action_id, BINDING.scope, BINDING.generation,
            "manual_confirmation", "synthetic-phone", sha))
    window = replace(BINDING.account.window, pid=BINDING.account.window.pid + 100,
        created_filetime=BINDING.account.window.created_filetime + 100, hwnd=BINDING.account.window.hwnd + 100)
    binding = replace(BINDING, account=replace(BINDING.account, window=window))
    return binding, {name: True for name in READY_FIELDS}


def test_readmission_needs_explicit_fresh_native_checks_and_does_not_unblock(tmp_path):
    with Ledger(tmp_path / "original.db") as ledger:
        binding, ready = seed(ledger)
        before = ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0]
        with pytest.raises(SocialError, match="missing"):
            require_background_admission(ledger, binding)
        checked = []
        context = revalidate_background(ledger, binding, ready,
            retired_generations=lambda store, window: checked.append(window) or True)
        assert context.check(ledger, binding)
        assert checked == [binding.account.window]
        assert context.descriptor["source_generation"] == BINDING.generation
        assert context.descriptor["generation"] == binding.generation
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0] == before
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]


@pytest.mark.parametrize("damage", ["phone", "ready", "extra", "old_alive", "code", "epoch"])
def test_readmission_fails_closed_on_unverified_prerequisites(tmp_path, damage):
    with Ledger(tmp_path / "original.db") as ledger:
        binding, ready = seed(ledger, phone=damage != "phone")
        if damage == "ready":
            ready["covered_by_other_app"] = False
        elif damage == "extra":
            ready["raw_text"] = "private"
        elif damage == "code":
            binding = replace(binding, account=replace(binding.account, window=replace(binding.account.window, dll_sha256="e" * 64)))
        elif damage == "epoch":
            binding = replace(binding, account=replace(binding.account, db_epoch="new-epoch"))
        with pytest.raises(SocialError):
            revalidate_background(ledger, binding, ready, retired_generations=lambda *_: damage != "old_alive")
        assert ledger._db.execute("SELECT SUM(input_permitted),SUM(submit_permitted) FROM wechat_send_operations").fetchone()[:] == (1, 1)


@pytest.mark.parametrize("damage", ["expired", "target", "revoked"])
def test_readmission_expires_and_rechecks_original_evidence(tmp_path, damage):
    tick = [1_800_000_000.0]
    with Ledger(tmp_path / "original.db", now=lambda: tick[0]) as ledger:
        binding, ready = seed(ledger)
        context = revalidate_background(ledger, binding, ready, retired_generations=lambda *_: True)
        if damage == "expired":
            tick[0] += 21
        elif damage == "target":
            binding = replace(binding, account=replace(binding.account, window=replace(binding.account.window, hwnd=999)))
        else:
            ledger._db.execute("DELETE FROM delivery_evidence WHERE kind='manual_confirmation'")
        with pytest.raises(SocialError):
            context.check(ledger, binding)


def test_review_consumption_needs_same_readmission_and_new_uncertainty_blocks_reuse(tmp_path):
    with Ledger(tmp_path / "original.db") as ledger:
        binding, ready = seed(ledger)
        context = revalidate_background(ledger, binding, ready, retired_generations=lambda *_: True)
        BridgeStore(ledger).bind(binding, mode="draft")
        facts = dict(ready, observed_at=ledger.now(), binding_generation=binding.generation,
                     prior_input_context_reconciled=True, background_admission_verified=True)
        review = channel_review.authorize(ledger, binding, INTEGRATED_REQUEST, INTEGRATED_MARKER, facts,
            approval_ref="bounded-service-batch", backend_kind="integrated_background_bmp", background_readmission=context)
        state = SendState(ledger)
        with pytest.raises(SocialError, match="context_invalid"):
            state.queue_marker(binding, INTEGRATED_REQUEST, INTEGRATED_MARKER, review_id=review)
        aid = state.queue_marker(binding, INTEGRATED_REQUEST, INTEGRATED_MARKER, review_id=review, background_readmission=context)
        action = ledger.claim(aid)
        op = state.begin(binding, action, {"selected": Cursor(0, 0)})
        state.permit(op, binding, action, "INPUT_PERMITTED")
        state.finish(op, reaped=True, restored=True, reason="synthetic_input_uncertain")
        ledger.finish_delivery(aid, "UNCONFIRMED", "synthetic_input_uncertain")
        with pytest.raises(SocialError, match="unreconciled"):
            context.check(ledger, binding)
        with pytest.raises(SocialError, match="unreconciled"):
            revalidate_background(ledger, binding, ready, retired_generations=lambda *_: True)


@pytest.mark.parametrize("qualified", [False, True])
def test_changed_build_requires_explicit_pinned_qualification_in_addition_to_native_readiness(tmp_path, qualified):
    with Ledger(tmp_path / "original.db") as ledger:
        binding, ready = seed(ledger)
        binding = replace(binding, account=replace(binding.account, window=replace(binding.account.window,
            dll_sha256="e" * 64, exe_sha256="f" * 64)))
        def compatibility(old, new):
            return qualified and old == BINDING.account.window and new == binding.account.window
        if not qualified:
            with pytest.raises(SocialError, match="incompatible"):
                revalidate_background(ledger, binding, ready, retired_generations=lambda *_: True, compatible_builds=compatibility)
        else:
            context = revalidate_background(ledger, binding, ready, retired_generations=lambda *_: True, compatible_builds=compatibility)
            assert context.check(ledger, binding)
            assert context.descriptor["build_transition_verified"]
            assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
