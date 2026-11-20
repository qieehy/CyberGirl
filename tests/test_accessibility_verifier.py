"""Critical transaction failures only; never open or modify a native process."""

import pytest

from scripts.verify_wechat_accessibility import summarize_controls, transaction


class Memory:
    def __init__(self, initial=0, *, activation_failure=False, restoration_failure=False):
        self.value = initial
        self.writes = []
        self.activation_failure = activation_failure
        self.restoration_failure = restoration_failure

    def preflight(self):
        return {"pid": 123}

    def validate(self):
        pass

    def read(self):
        return self.value

    def write(self, value):
        self.writes.append(value)
        if value == 0 and self.restoration_failure:
            return  # API reports success but the byte did not change.
        self.value = value
        if value == 1 and self.activation_failure:
            raise OSError("write changed memory before reporting failure")


def test_default_read_only_and_prewrite_failures_never_modify_memory():
    memory = Memory()
    result = transaction(memory, lambda _: pytest.fail("default must not capture UI"))
    assert result["status"] == "preflight_ready" and memory.writes == []
    memory = Memory(initial=1)
    assert transaction(memory, lambda _: None, activate=True)["status"] == "already_enabled_no_write"
    assert memory.writes == []
    memory = Memory(initial=7)
    with pytest.raises(ValueError, match="unexpected byte"):
        transaction(memory, lambda _: None, activate=True)
    assert memory.writes == []

    memory = Memory()

    def broken_journal(event):
        if event["stage"] == "before_write":
            raise OSError("disk full")

    with pytest.raises(OSError):
        transaction(memory, lambda _: None, activate=True, record=broken_journal)
    assert memory.writes == []


def test_success_timeout_interrupt_and_uncertain_write_all_restore_original():
    for failure in (None, TimeoutError, KeyboardInterrupt):
        memory = Memory()

        def observe(_facts, error=failure, target=memory):
            assert target.value == 1
            if error:
                raise error()
            return {"node_count": 3, "chat_control_candidate": False}

        result = transaction(memory, observe, activate=True)
        assert result["restore_verified"] is True
        assert memory.value == 0 and memory.writes == [1, 0]
        assert result["status"] == ("experiment_failed_restored" if failure else "experiment_restored")
    memory = Memory(activation_failure=True)
    result = transaction(memory, lambda _: pytest.fail("must not observe after write failure"), activate=True)
    assert result["restore_verified"] and memory.value == 0 and memory.writes == [1, 0]


def test_failed_restore_is_not_hidden_by_observation_or_logging_errors():
    memory = Memory(restoration_failure=True)

    def broken_journal(event):
        if event["stage"] == "completed":
            raise OSError("disk full after experiment")

    def interrupted(_facts):
        raise TimeoutError()

    result = transaction(memory, interrupted, activate=True, record=broken_journal)
    assert result["status"] == "restore_failed" and result["restore_verified"] is False
    assert result["error_type"] == "TimeoutError" and result["journal_error_type"] == "OSError"
    assert memory.writes == [1, 0] and memory.value == 1

    memory = Memory()

    def concurrent_change(_facts):
        memory.value = 7
        return {}

    result = transaction(memory, concurrent_change, activate=True)
    assert result["status"] == "restore_failed"
    assert memory.value == 7 and memory.writes == [1]  # Never overwrite unexpected concurrent state.


def test_login_and_search_windows_cannot_be_mistaken_for_chat_controls():
    nodes = [
        {"index": 0, "parent": None, "class_name": "mmui::LoginWindow", "type": "ControlType.Window"},
        {"index": 1, "parent": 0, "class_name": "QLineEdit", "type": "ControlType.Edit"},
        {"index": 2, "parent": 0, "class_name": "QListView", "type": "ControlType.List"},
    ]
    window = {"raw": {"nodes": nodes, "bounded": False, "errors": []}}
    result = summarize_controls(window)
    assert result["window_kind"] == "login" and result["manual_login_or_main_window_needed"]
    assert not result["chat_control_candidate"]
    nodes[0]["class_name"] = "mmui::SearchMsgUniqueChatWindow"
    assert not summarize_controls(window)["chat_control_candidate"]
    nodes[0]["class_name"] = "mmui::MainWindow"
    assert summarize_controls(window)["chat_control_candidate"]
    window["raw"]["bounded"] = True
    assert not summarize_controls(window)["chat_control_candidate"]
