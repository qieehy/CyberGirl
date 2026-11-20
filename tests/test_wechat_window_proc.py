"""Bounded procedure metadata reads; opaque handles never become addresses."""

import json

import pytest

from tests.test_wechat_qt_memory import Memory
from wechat_agent.bridges.wechat.window_proc import inspect_window_procedures
from wechat_agent.social.models import SocialError


def fixture():
    memory = Memory()
    memory.cells[(memory.base + 0x1000, 128)] = b"A" * 128
    return memory, lambda rva, size: b"A" * size


def test_matching_class_and_window_code_is_lossy_metadata_not_an_invocation():
    memory, static = fixture()
    result = inspect_window_procedures(memory, static, lambda kind: memory.base + 0x1000)
    assert result["same_procedure"] and result["stable"] and result["lookup_bytes"] == 256
    assert result["methods_executed"] == 0 and not result["submission_enabled"]
    assert str(memory.base + 0x1000) not in json.dumps(result)


def test_class_can_be_observed_independently_without_claiming_a_window_entry():
    memory, static = fixture()
    calls = []

    def get(kind):
        calls.append(kind)
        assert kind == "class"  # Do not retry the separately denied window query.
        return memory.base + 0x1000

    result = inspect_window_procedures(memory, static, get, kinds=("class",))
    assert calls == ["class", "class"] and result["lookup_bytes"] == 128
    assert result["class_procedure_observed"] and not result["window_procedure_observed"]
    assert result["same_procedure"] is None and not result["submission_enabled"]


@pytest.mark.parametrize("kinds", [(), ("class", "class"), ("other",), ["window"]])
def test_invalid_scope_cannot_read_or_query(kinds):
    memory, static = fixture()
    with pytest.raises(SocialError, match="invalid_procedure_observation_scope"):
        inspect_window_procedures(memory, static, lambda kind: pytest.fail("unexpected query"), kinds=kinds)


@pytest.mark.parametrize("kind", ["opaque", "changed_bytes", "non_executable", "replaced"])
def test_untrusted_procedure_values_or_changes_are_rejected_before_any_invocation(kind):
    memory, static = fixture()
    calls = []

    def get(name):
        calls.append(name)
        if kind == "opaque" or (kind == "replaced" and len(calls) > 2):
            return 0xFFFF000000000001
        return memory.base + 0x1000

    if kind == "changed_bytes":
        def static(rva, size):
            return b"B" * size
    elif kind == "non_executable":
        def static(rva, size):
            raise ValueError("synthetic data section")
    with pytest.raises(SocialError):
        inspect_window_procedures(memory, static, get)
    if kind == "opaque":
        assert calls == ["window"]
