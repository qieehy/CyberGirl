"""Explicit operator private background sends in the original ledger/journal."""
import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from ..bridges.wechat.background_journal import BackgroundIntentJournal
from ..bridges.wechat.background_lock import writer_locks
from ..bridges.wechat.background_process import BackgroundWorker
from ..bridges.wechat.contracts import Cursor, DeliveryEvidence, digest
from ..bridges.wechat.private_receive import private_scope
from ..bridges.wechat.private_send import require_private_target
from ..bridges.wechat.send_process import python_worker_command
from ..bridges.wechat.send_state import SendState
from ..bridges.wechat.sender import AccessibilityLease
from ..bridges.wechat.store import BridgeStore
from ..social.models import OutgoingAction, SocialError, identifier


class PrivateSendState(SendState):
    def _action(self, db, binding, action):
        require_private_target(binding, action, observed_account=binding.account.account_id,
                               observed_username=binding.username, observed_window=binding.account.window)
        BridgeStore._binding(db, binding)
        row = db.execute("""SELECT i.*,o.text AS reply,o.state AS delivery_state,o.expires_at FROM inbox i
            JOIN outbox o USING(event_id) WHERE o.action_id=?""", (action.action_id,)).fetchone()
        control = db.execute("SELECT * FROM control").fetchone()
        scope = db.execute("SELECT * FROM scopes WHERE scope=?", (binding.scope.key(),)).fetchone()
        if (not row or row["delivery_state"] != "SENDING" or row["scope"] != binding.scope.key()
                or row["bridge_generation"] != binding.generation or row["reply"] != action.text
                or row["message_id"] != action.reply_to or row["expires_at"] <= self.ledger.now()
                or row["origin"] != "operator" or row["reason"] != "private_background_operator"
                or control["paused"] or control["revision"] != row["global_revision"]
                or control["persona_version"] != row["persona_version"] or scope["context_version"] != row["context_version"]):
            raise SocialError("stale_private_send_action")
        return row

    def begin_private(self, binding, action, baselines):
        operation = uuid.uuid4().hex
        with self.ledger._transaction() as db:
            self._action(db, binding, action)
            if db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0 OR worker_reaped IS NOT 1 OR restore_verified IS NOT 1").fetchone():
                raise SocialError("private_prior_worker_cleanup_required")
            if db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] not in (None, "submission_or_draft_requires_review", "reviewed_trial_requires_review"):
                raise SocialError("private_channel_cleanup_required")
            if db.execute("""SELECT 1 FROM wechat_send_operations s WHERE s.scope=? AND s.input_permitted=1
                AND NOT EXISTS(SELECT 1 FROM delivery_evidence e WHERE e.action_id=s.action_id AND e.kind='local_echo')""", (binding.scope.key(),)).fetchone():
                raise SocialError("private_prior_send_requires_review")
            db.execute("""INSERT INTO wechat_send_operations(operation_id,action_id,scope,generation,content_sha256,
                baselines,window_binding,phase,created_at) VALUES(?,?,?,?,?,?,?,'PRECHECK',?)""",
                (operation, action.action_id, binding.scope.key(), binding.generation, hashlib.sha256(action.text.encode()).hexdigest(),
                 json.dumps({k: [v.sort_seq, v.local_id] for k,v in baselines.items()}, sort_keys=True),
                 json.dumps(asdict(binding.account.window), sort_keys=True), self.ledger.now()))
        return operation


class PrivateJournal(BackgroundIntentJournal):
    def workbench_action(self, state, action):
        return True

    def _operation(self, db):
        require_private_target(self.binding, self.action, observed_account=self.binding.account.account_id,
            observed_username=self.binding.username, observed_window=self.binding.account.window)
        row = db.execute("SELECT * FROM wechat_send_operations WHERE operation_id=?", (self.operation_id,)).fetchone()
        if (not row or row["finished"] or row["action_id"] != self.action.action_id
                or row["scope"] != self.binding.scope.key() or row["generation"] != self.binding.generation
                or row["content_sha256"] != hashlib.sha256(self.action.text.encode()).hexdigest()):
            raise SocialError("private_send_operation_mismatch")
        return row


class PrivateBackgroundSender:
    def __init__(self, ledger, receiver, scope_file):
        self.ledger, self.receiver, self.binding = ledger, receiver, receiver.binding
        self.scope_file = Path(scope_file).resolve()
        self.scope_raw = self.scope_file.read_bytes()
        scope = private_scope(json.loads(self.scope_raw))
        if scope["username"] != self.binding.username or hashlib.sha256(self.binding.account.account_id.encode()).hexdigest() != scope["account_fingerprint"]:
            raise SocialError("private_sender_scope_mismatch")
        self.state = PrivateSendState(ledger)
        self._held_locks = None

    def result(self, action_id):
        row = self.ledger._db.execute("SELECT state,reason FROM outbox WHERE action_id=?", (action_id,)).fetchone()
        return dict(action_id=action_id, **dict(row), local_echo=bool(self.ledger._db.execute(
            "SELECT 1 FROM delivery_evidence WHERE action_id=? AND kind='local_echo'", (action_id,)).fetchone()))

    def check_ready(self):
        db=self.ledger._db
        BridgeStore._binding(db,self.binding)
        if db.execute("SELECT 1 FROM wechat_send_operations WHERE finished=0 OR worker_reaped IS NOT 1 OR restore_verified IS NOT 1").fetchone():
            raise SocialError('private_prior_worker_cleanup_required')
        if db.execute("""SELECT 1 FROM wechat_send_operations s WHERE s.scope=? AND s.input_permitted=1
            AND NOT EXISTS(SELECT 1 FROM delivery_evidence e WHERE e.action_id=s.action_id AND e.kind='local_echo')""",(self.binding.scope.key(),)).fetchone():
            raise SocialError('private_prior_send_requires_review')
        import ctypes as C
        user=C.WinDLL('user32',use_last_error=True)
        user.IsIconic.argtypes,user.IsIconic.restype=[C.c_void_p],C.c_int
        user.GetWindowThreadProcessId.argtypes=[C.c_void_p,C.POINTER(C.c_uint32)]
        owner=C.c_uint32()
        user.GetWindowThreadProcessId(self.binding.account.window.hwnd,C.byref(owner))
        if owner.value!=self.binding.account.window.pid:
            raise SocialError('private_send_window_changed')
        if user.IsIconic(self.binding.account.window.hwnd):
            raise SocialError('standalone_window_minimized')

    async def send(self, request_id, text, *, draft_id=None, expected_version=None, guard=None):
        identifier(request_id)
        action_id = digest([self.binding.scope.key(), request_id, "private-background"])
        action = OutgoingAction(action_id, self.binding.scope, "operator:" + action_id, text)
        require_private_target(self.binding, action, observed_account=self.binding.account.account_id,
            observed_username=self.binding.username, observed_window=self.binding.account.window)
        with self.ledger._transaction() as db:
            old = db.execute("SELECT text FROM outbox WHERE action_id=?", (action_id,)).fetchone()
            if old:
                if old[0] != text:
                    raise SocialError("event_identity_conflict")
                return self.result(action_id)
        # Fresh receive + physical tail precede the operator request, so old echoes never count.
        for _ in range(8):
            if not (await self.receiver.poll())["more"]:
                break
        else:
            raise SocialError("private_send_receive_bound")
        async with self.receiver.rpc_lock:
            manifest = await self.receiver.worker.rpc("echo_baseline")
        registered = self.receiver.catalog.checkpoints()
        if set(manifest["shards"]) != set(registered):
            raise SocialError("private_send_shards_changed")
        baselines = {}
        for name,item in manifest["shards"].items():
            if registered[name]["shard"] != name + ":" + item["identity"]:
                raise SocialError("private_send_shards_changed")
            baselines[registered[name]["shard"]] = Cursor(**item["tail"])
        with self.ledger._transaction() as db:
            BridgeStore._binding(db, self.binding)
            if guard is not None:
                guard()
            if draft_id is not None:
                from .state import WorkbenchState
                _,revision=WorkbenchState(self.ledger,self.binding).require_ready(db,draft_id,expected_version)
                if revision['text'] != text:
                    raise SocialError('version_conflict')
            control = db.execute("SELECT * FROM control").fetchone()
            scope = db.execute("SELECT * FROM scopes WHERE scope=?", (self.binding.scope.key(),)).fetchone()
            if control["paused"]:
                raise SocialError("bridge_paused")
            now = self.ledger.now()
            db.execute("""INSERT INTO inbox(event_id,scope,message_id,fingerprint,text,sender_id,occurred_at,
                source_verified,is_self,mention_self,received_at,policy_revision,global_revision,context_version,
                state,reason,persona_version,bridge_generation,origin)
                VALUES(?,?,?,?,?,'local-operator',?,0,0,0,?,?,?,?,'DECIDED','private_background_operator',?,?,'operator')""",
                (action_id, self.binding.scope.key(), action.reply_to, digest([text, self.binding.generation]),
                 "后台私聊发送请求", now, now, scope["revision"], control["revision"], scope["context_version"],
                 control["persona_version"], self.binding.generation))
            db.execute("INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,?,?,'SENDING',?)", (action_id,action_id,text,now+60))
            db.execute("INSERT INTO attempts VALUES(?,?,'SENDING',NULL)", (action_id, now))
            if draft_id is not None:
                db.execute("UPDATE workbench_drafts SET state='SUBMITTED',action_id=? WHERE draft_id=?",(action_id,draft_id))
        worker = lease = locks = operation = None
        reaped = restored = True
        reason = "private_background_not_started"
        try:
            operation = self.state.begin_private(self.binding, action, baselines)
            journal = PrivateJournal(self.state, operation, self.binding, action)
            from scripts.wechat_42013_profile import activate

            from .native import native_source_root
            activate()
            from scripts.verify_wechat_accessibility import NativeMemory
            window = self.binding.account.window
            locks = writer_locks(self.binding)
            lease = AccessibilityLease(NativeMemory(window.pid, hwnd=window.hwnd, created_filetime=window.created_filetime, activate=True),
                window, lambda stage, original: self.state.journal(operation, stage, original=original))
            lease.open()
            worker = BackgroundWorker(python_worker_command(native_source_root()/"scripts/wechat_private_send_worker.py",
                "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
                "--scope-file", str(self.scope_file), "--scope-sha256", hashlib.sha256(self.scope_raw).hexdigest(),
                "--text-sha256", hashlib.sha256(text.encode()).hexdigest(), "--allow-control",
                "--allow-foreground"), record=journal.record)
            async with asyncio.timeout(25):
                await worker.start(operation, self.binding, action)
                readiness = (await worker._rpc("prepare"))["readiness"]
                if set(readiness) != {"account_and_target_verified", "both_draft_patterns_empty", "window_messages_only", "canonical_private_identity_verified"} or any(v is not True for v in readiness.values()):
                    raise SocialError("private_preparation_incomplete")
                if guard is not None:
                    guard()
                result = await worker._rpc("run_background")
                from ..bridges.wechat.background_completion import clean_background_result
                if not clean_background_result(result, text, allow_foreground=True):
                    raise SocialError("private_background_cleanup_not_verified")
                reason = result["control_result"]["reason"]
        except Exception as exc:
            reason = exc.code if isinstance(exc, SocialError) else type(exc).__name__
        finally:
            if worker:
                try:
                    async with asyncio.timeout(3):
                        reaped = await worker.stop() is True
                except BaseException:
                    reaped = False
            if lease:
                restored = lease.close(restore=reaped) is True
            if locks and reaped:
                locks.close()
            elif locks:
                self._held_locks = locks
            if operation:
                touched = self.state.finish(operation, reaped=reaped, restored=restored, reason=reason)
            else:
                touched = False
            with self.ledger._transaction() as db:
                db.execute("UPDATE outbox SET state=?,reason=? WHERE action_id=?", ("UNCONFIRMED" if touched else "FAILED", reason, action_id))
                db.execute("UPDATE attempts SET state=?,finished_at=? WHERE action_id=?", ("UNCONFIRMED" if touched else "FAILED", self.ledger.now(), action_id))
        if operation and reaped and restored:
            records = []
            previous = self.receiver.on_committed
            def committed(session, point, batch, accepted):
                records.extend(batch)
                if previous:
                    previous(session,point,batch,accepted)
            self.receiver.on_committed = committed
            try:
                async with asyncio.timeout(15):
                    for _ in range(5):
                        await self.receiver.poll()
                        try:
                            self.state.local_echo(self.binding, action_id, tuple(records))
                            break
                        except SocialError as exc:
                            if exc.code != "local_echo_not_unique":
                                break
                        await asyncio.sleep(1)
            finally:
                self.receiver.on_committed = previous
        return self.result(action_id)

    def reconcile_existing(self, action_id):
        """Link already persisted native self-echo; never input or resubmit."""
        db = self.ledger._db
        operation = db.execute("""SELECT s.*,o.text,o.state,i.reason AS origin_reason FROM wechat_send_operations s
            JOIN outbox o USING(action_id) JOIN inbox i USING(event_id) WHERE s.action_id=? AND s.scope=?""",
            (action_id,self.binding.scope.key())).fetchone()
        if (not operation or operation["origin_reason"] != "private_background_operator"
                or not operation["finished"] or not operation["worker_reaped"] or not operation["restore_verified"]):
            raise SocialError("private_echo_operation_not_clean")
        # Compatibility repair for the first two operations created before attempts were wired.
        with self.ledger._transaction() as tx:
            finished = tx.execute("SELECT MAX(recorded_at) FROM wechat_send_journal WHERE operation_id=?",(operation["operation_id"],)).fetchone()[0]
            tx.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?)",(action_id,operation["created_at"],operation["state"],finished))
        if not operation["submit_permitted"]:
            return self.result(action_id)
        baselines = {k:Cursor(*v) for k,v in json.loads(operation["baselines"]).items()}
        matches = db.execute("""SELECT b.message_id,b.sort_seq,b.local_id,c.shard FROM bridge_records b
            JOIN bridge_message_payloads p USING(message_id) JOIN bridge_cursors c USING(source_key)
            WHERE c.scope=? AND c.db_epoch=? AND p.sender_id=? AND p.text=? AND p.message_type='text'
            AND b.disposition='self_echo'""", (self.binding.scope.key(),self.binding.account.db_epoch,self.binding.account.self_id,operation["text"])).fetchall()
        matches = [r for r in matches if r["shard"] in baselines and Cursor(r["sort_seq"],r["local_id"]) > baselines[r["shard"]]]
        if len(matches) != 1:
            raise SocialError("local_echo_not_unique")
        mid = matches[0]["message_id"]
        evidence = DeliveryEvidence(digest([operation["operation_id"],mid]),action_id,self.binding.scope,
            operation["generation"],"local_echo",operation["operation_id"],operation["content_sha256"],mid)
        BridgeStore(self.ledger).record_evidence(evidence)
        return self.result(action_id)
