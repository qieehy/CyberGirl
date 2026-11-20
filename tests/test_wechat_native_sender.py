"""Native backend action boundaries with synthetic UI/memory only."""

import asyncio
import hashlib
import sqlite3
from dataclasses import asdict, replace

import pytest

from tests.test_wechat_bridge import BINDING as SOURCE_BINDING
from tests.test_wechat_bridge import NOW, WINDOW, bind, queue
from wechat_agent.bridges.wechat import native_sender
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.native_sender import NativeSendBackend
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.send_worker import WorkerSession
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import OutgoingAction, SocialError

BINDING = replace(SOURCE_BINDING, account=replace(SOURCE_BINDING.account, self_id=SOURCE_BINDING.account.account_id))
ACTION = OutgoingAction("fixed-native-action", BINDING.scope, "fixed-native-source", "W2B-synthetic-once")


class Ui:
    text = ""
    focused = False
    token = "a" * 64
    enabled = True
    clear_after_submit = True

    def __init__(self):
        self.calls = []

    def observe(self):
        return dict(runtime_ids={k: [3, 0x80000001] for k in ("selected_session", "chat_input", "page", "button")},
                    input_token=self.token, focused=self.focused, empty=not self.text,
                    draft_sha256=hashlib.sha256(self.text.encode()).hexdigest(), send_enabled=self.enabled)

    def focus_once(self, token):
        self.calls.append("focus")
        self.focused = True

    def input_once(self, token, text):
        self.calls.append("input")
        self.text = text

    def submit_once(self, token):
        self.calls.append("submit")
        if self.clear_after_submit:
            self.text = ""

    def draft_patterns(self, token):
        shape = dict(value_sha256=hashlib.sha256(self.text.encode()).hexdigest(), value_length=len(self.text), value_empty=not self.text,
                     text_status="read", text_sha256=hashlib.sha256(self.text.encode()).hexdigest(), text_length=len(self.text),
                     text_empty=not self.text, exact_equal=True, trailing_linebreaks_only_difference=False)
        return dict(input_token=token, comparison=shape, send_enabled=self.enabled)


def setup(monkeypatch):
    verified = []

    def verify(*args, **kwargs):
        verified.append("native_check")

    monkeypatch.setattr(native_sender, "verify_current_filehelper", verify)
    ui = Ui()
    backend = NativeSendBackend(WINDOW, hashlib.sha256(BINDING.account.account_id.encode()).hexdigest(),
                                lambda _: (object(), lambda: BINDING.account.account_id, ui))
    backend.configure(BINDING, ACTION)
    # Synthetic UI implements an effect; the live build's focus-only adapter
    # is deliberately denied by the production submission-path verifier.
    monkeypatch.setattr(backend, "_require_submit_path", lambda: None)
    return backend, ui, verified


def test_native_focus_input_submit_each_recheck_and_are_single_use(monkeypatch):
    backend, ui, verified = setup(monkeypatch)
    backend.prepare_input_focus()
    initial = backend.inspect()
    backend.input_text(ACTION.text, initial.input_token)
    backend.inspect().verify(BINDING, ACTION, initial=initial)
    backend.submit_once(initial.input_token)
    assert ui.calls == ["focus", "input", "submit"] and len(verified) >= 6
    for effect in (backend.prepare_input_focus, lambda: backend.input_text(ACTION.text, initial.input_token),
                   lambda: backend.submit_once(initial.input_token)):
        with pytest.raises(SocialError):
            effect()
    assert ui.calls == ["focus", "input", "submit"]


@pytest.mark.parametrize("failure", ["draft", "native_identity", "focus", "input_token", "post_text", "post_token", "send_disabled"])
def test_native_change_never_reaches_next_effect(monkeypatch, failure):
    backend, ui, _ = setup(monkeypatch)
    if failure == "draft":
        ui.text = "private draft preserved"
        with pytest.raises(SocialError):
            backend.prepare_input_focus()
        assert not ui.calls
        return
    if failure == "native_identity":
        def reject(*args, **kwargs):
            raise SocialError("native_current_session_not_filehelper")
        monkeypatch.setattr(native_sender, "verify_current_filehelper", reject)
        with pytest.raises(SocialError):
            backend.prepare_input_focus()
        assert not ui.calls
        return
    backend.prepare_input_focus()
    initial = backend.inspect()
    if failure in {"focus", "input_token"}:
        if failure == "focus":
            ui.focused = False
        else:
            ui.token = "b" * 64
        with pytest.raises(SocialError):
            backend.input_text(ACTION.text, initial.input_token)
        assert ui.calls == ["focus"]
        return
    backend.input_text(ACTION.text, initial.input_token)
    if failure == "post_text":
        ui.text += "changed"
    elif failure == "post_token":
        ui.token = "b" * 64
    else:
        ui.enabled = False
    with pytest.raises(SocialError):
        backend.submit_once(initial.input_token)
    assert ui.calls == ["focus", "input"]


def test_focus_permit_is_durable_single_use_and_checks_pause(tmp_path):
    with Ledger(tmp_path / "focus.db", now=lambda: NOW) as ledger:
        store = bind(ledger, mode="draft")
        action = ledger.claim(queue(ledger, store))
        state = SendState(ledger)
        operation = state.begin(SOURCE_BINDING,
                                action, {"message_0": Cursor(10, 1)})
        ledger.pause(True)
        with pytest.raises(SocialError):
            state.permit_focus(operation, SOURCE_BINDING, action)
        assert ledger._db.execute("SELECT COUNT(*) FROM wechat_send_journal").fetchone()[0] == 0
    # A separate action is needed after pause invalidates the first one.
    with Ledger(tmp_path / "focus-normal.db", now=lambda: NOW) as ledger:
        store = bind(ledger, mode="draft")
        action = ledger.claim(queue(ledger, store))
        state = SendState(ledger)
        operation = state.begin(SOURCE_BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit_focus(operation, SOURCE_BINDING, action)
        with pytest.raises(SocialError, match="focus_permit_already_used"):
            state.permit_focus(operation, SOURCE_BINDING, action)
        assert ledger._db.execute("SELECT input_permitted FROM wechat_send_operations").fetchone()[0] == 0


def test_capabilities_grants_no_input_state_and_focus_permit_cannot_replay(monkeypatch):
    backend, ui, _ = setup(monkeypatch)
    backend.binding = backend.action = None
    session = WorkerSession(backend)

    def command(name, **payload):
        return session.handle(dict(version=1, operation_id="native-op", sequence=session.sequence + 1, command=name, **payload))

    command("init", binding=asdict(BINDING), action=asdict(ACTION))
    assert not command("capabilities")["result"]["observation"]["focused"]
    assert session.initial is None and not ui.calls
    command("focus_once")
    with pytest.raises(SocialError):
        command("focus_once")
    assert ui.calls == ["focus"] and not ui.text


def test_selected_source_queries_only_new_filehelper_rows_and_fences_account_or_file_change(tmp_path, monkeypatch):
    from scripts import wechat_filehelper_source as module

    class Memory:
        checks = 0
        closed = False

        def validate(self):
            self.checks += 1

        def read_bytes(self, address, size):
            raise AssertionError("synthetic account reader must be injected")

        def close(self):
            self.closed = True

    source = module.FilehelperSource.__new__(module.FilehelperSource)
    source.memory = Memory()
    source.config = 123
    source.username = BINDING.account.account_id
    source.binding = BINDING
    source.shards = {}
    calls = []
    for name in ("message_0.db", "message_1.db"):
        path = tmp_path / name
        path.write_bytes(b"synthetic encrypted page placeholder")
        source.shards[name] = (path, b"k" * 32, b"s" * 16, path.stat().st_ino)

    def snapshot(path, key, salt):
        calls.append(path.name)
        connection = sqlite3.connect(":memory:")
        table = "Msg_" + hashlib.md5(b"filehelper", usedforsecurity=False).hexdigest()
        connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
        connection.execute("INSERT INTO Name2Id VALUES(?)", (source.username,))
        connection.execute(f"CREATE TABLE {table}(local_id,local_type,real_sender_id,create_time,message_content,sort_seq)")
        connection.execute(f"INSERT INTO {table} VALUES(1,1,1,?, 'old body never returned',10)", (NOW,))
        connection.execute(f"INSERT INTO {table} VALUES(2,1,1,?,?,11)", (NOW, ACTION.text))
        return connection, {}

    monkeypatch.setattr(module, "open_memory_snapshot", snapshot)
    monkeypatch.setattr(module, "remote_bytes", lambda *args, **kwargs: source.username.encode())
    assert set(source.baselines()) == {"message_0.db", "message_1.db"}
    records = source.records({name: Cursor(10, 1) for name in source.shards})
    assert len(records) == 2 and all(row.text == ACTION.text and row.position == Cursor(11, 2) for row in records)
    assert source.memory.checks == 4 and calls == ["message_0.db", "message_1.db"] * 2
    with pytest.raises(SocialError, match="source_baseline_shards_mismatch"):
        source.records({"message_0.db": Cursor(10, 1)})
    monkeypatch.setattr(module, "remote_bytes", lambda *args, **kwargs: b"other_account")
    with pytest.raises(SocialError, match="source_account_changed"):
        source.baselines()
    monkeypatch.setattr(module, "remote_bytes", lambda *args, **kwargs: source.username.encode())
    path, key, salt, inode = source.shards["message_0.db"]
    source.shards["message_0.db"] = path, key, salt, inode + 1
    with pytest.raises(SocialError, match="selected_shard_replaced"):
        source.baselines()
    source.close()
    assert not source.shards and source.memory.closed


@pytest.mark.parametrize("mode", ["normal", "paused_after_preparation", "preparation_timeout"])
def test_bounded_readonly_preparation_keeps_a_separate_effect_window_and_rechecks_policy(tmp_path, mode):
    from tests.test_wechat_sender import Memory, NoModel, Worker, baselines
    from wechat_agent.bridges.wechat.sender import AccessibilityLease, WeChatSendAdapter
    from wechat_agent.social.service import SocialService

    async def run():
        with Ledger(tmp_path / (mode + ".db"), now=lambda: NOW) as ledger:
            store = bind(ledger, mode="draft")
            action_id = queue(ledger, store)

            class PreparedWorker(Worker):
                async def warmup(self):
                    await asyncio.sleep(0.06)
                    if mode == "paused_after_preparation":
                        ledger.pause(True)
                    return await self.inspect()

                async def prepare_input_focus(self):
                    self.calls.append("focus")

            worker, memory = PreparedWorker(), Memory()
            adapter = WeChatSendAdapter(ledger, SOURCE_BINDING, worker_factory=lambda: worker,
                lease_factory=lambda window, log: AccessibilityLease(memory, window, log), baseline_provider=baselines,
                timeout=0.04, preparation_timeout=0.02 if mode == "preparation_timeout" else 0.2)
            async with SocialService(ledger, NoModel(), [adapter], dispatch_timeout=1) as service:
                outcome = await service.dispatch(action_id)
            assert memory.value == 0 and memory.closed
            if mode == "normal":
                assert outcome["state"] == "UNCONFIRMED"
                assert worker.calls == ["start", "focus", "input", "submit", "stop"]
            else:
                assert outcome["state"] == "FAILED" and worker.calls == ["start", "stop"]
                assert ledger._db.execute("SELECT input_permitted FROM wechat_send_operations").fetchone()[0] == 0

    asyncio.run(run())


def test_fixed_marker_replacement_requires_proven_zero_input_and_restoration():
    from scripts.probe_wechat_native_sender import require_preinput_replacement

    old = dict(action_id="old", input_permitted=0, delivery_state="FAILED", finished=1, worker_reaped=1, restore_verified=1)
    require_preinput_replacement([old], "new")
    for changes in (dict(input_permitted=1), dict(delivery_state="UNCONFIRMED"), dict(finished=0),
                    dict(worker_reaped=0), dict(restore_verified=0)):
        with pytest.raises(SocialError):
            require_preinput_replacement([old | changes], "new")
    # The existing request can be looked up idempotently; it never creates a new attempt.
    require_preinput_replacement([old | dict(input_permitted=1, delivery_state="UNCONFIRMED")], "old")
