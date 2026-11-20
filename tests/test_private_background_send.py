"""Critical private target/order/idempotency cases; no real WeChat access."""
import asyncio
import hashlib
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.wechat_background_control import WorkbenchControl
from tests.test_wechat_background_control import BINDING, Rig
from wechat_agent.bridges.wechat.contracts import require_send_target
from wechat_agent.bridges.wechat.private_send import PrivateInputObservation, require_private_target
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import OutgoingAction, SocialError
from wechat_agent.workbench.private_sender import PrivateBackgroundSender

PEER = replace(BINDING, username="wxid_private", evidence_ref="r3-selected-native:"+hashlib.sha256(b"wxid_private").hexdigest())
ACTION = OutgoingAction("private-once", PEER.scope, "operator-once", "Cybergirl测试")


def test_private_target_does_not_weaken_legacy_filehelper_guard():
    args = dict(observed_account=PEER.account.account_id, observed_username=PEER.username, observed_window=PEER.account.window)
    require_private_target(PEER, ACTION, **args)
    with pytest.raises(SocialError, match="wechat_send_target_denied"):
        require_send_target(PEER, ACTION, **args)
    with pytest.raises(SocialError, match="private_send_target_denied"):
        require_private_target(PEER, ACTION, **{**args, "observed_username":"wxid_other"})


def test_private_background_posts_one_return_and_refuses_duplicate():
    rig = Rig()
    def observe():
        return PrivateInputObservation(PEER.account.account_id,PEER.username,PEER.account.window,
            "native_username",rig.token,False,not rig.body,hashlib.sha256(rig.body.encode()).hexdigest()),rig.enabled,rig.xy
    original = rig.post
    def post(message,wparam,lparam):
        result = original(message,wparam,lparam)
        if message == 0x101:
            rig.body = ""
        return result
    rig.post = post
    control = WorkbenchControl(PEER,ACTION,observe=observe,guard=rig.guard,transport=rig,record=rig.record,
        clock=lambda:rig.now,sleep=rig.sleep)
    result = control.run_once(write_guard=nullcontext())
    assert result.submit_possible and result.focus_cleanup_verified
    assert sum(p[0]==0x100 for p in rig.posts)==1 and sum(p[0]==0x101 for p in rig.posts)==1
    with pytest.raises(SocialError, match="already_used"):
        control.run_once(write_guard=nullcontext())


def test_same_private_request_never_dispatches_twice(tmp_path):
    async def run():
        with Ledger(tmp_path/"idem.db") as ledger:
            from wechat_agent.bridges.wechat.contracts import digest
            aid=digest([PEER.scope.key(),"same","private-background"])
            ledger._db.execute("PRAGMA foreign_keys=OFF")
            ledger._db.execute("INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,? ,?,'UNCONFIRMED',?)",(aid,"fixture",ACTION.text,ledger.now()+60))
            sender=object.__new__(PrivateBackgroundSender)
            sender.ledger,sender.binding=ledger,PEER
            sender.receiver=SimpleNamespace() # Any attempted poll/dispatch would fail.
            result=await sender.send("same",ACTION.text)
            assert result["action_id"]==aid and result["state"]=="UNCONFIRMED"
            with pytest.raises(SocialError,match="identity_conflict"):
                await sender.send("same","different")
    asyncio.run(run())


def test_existing_echo_reconciliation_repairs_attempt_without_resending(tmp_path):
    import json
    from dataclasses import asdict

    from wechat_agent.bridges.wechat.contracts import Cursor, SourceRecord
    from wechat_agent.bridges.wechat.store import BridgeStore
    from wechat_agent.workbench.private_sender import PrivateSendState

    with Ledger(tmp_path/"echo.db") as ledger:
        db=ledger._db
        store=BridgeStore(ledger)
        store.bind(PEER)
        store.baseline(PEER,"shard",Cursor(0,0))
        now=ledger.now()
        db.execute("""INSERT INTO inbox(event_id,scope,message_id,fingerprint,text,sender_id,occurred_at,
            source_verified,is_self,mention_self,received_at,policy_revision,global_revision,context_version,
            state,reason,persona_version,bridge_generation,origin)
            VALUES('op',?,'operator-once','fp','operator','local',?,0,0,0,?,1,1,0,'DECIDED','private_background_operator',1,?,'operator')""",
            (PEER.scope.key(),now,now,PEER.generation))
        db.execute("INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,'op',?,'UNCONFIRMED',?)",(ACTION.action_id,ACTION.text,now+60))
        db.execute("""INSERT INTO wechat_send_operations(operation_id,action_id,scope,generation,content_sha256,
            baselines,window_binding,phase,input_permitted,submit_permitted,finished,worker_reaped,restore_verified,created_at)
            VALUES('operation',?,?,?,?,?,?,'FINISHED',1,1,1,1,1,?)""",(ACTION.action_id,PEER.scope.key(),PEER.generation,
            hashlib.sha256(ACTION.text.encode()).hexdigest(),json.dumps({'shard':[0,0]}),json.dumps(asdict(PEER.account.window)),now))
        db.execute("INSERT INTO wechat_send_journal(operation_id,stage,recorded_at) VALUES('operation','GATE_RESTORED',?)",(now,))
        record=SourceRecord(PEER.account.account_id,PEER.account.db_epoch,PEER.username,'dm','shard',1,1,PEER.account.self_id,now,now,ACTION.text)
        store.ingest(PEER,'shard',Cursor(0,0),(record,))
        sender=object.__new__(PrivateBackgroundSender)
        sender.ledger,sender.binding,sender.state=ledger,PEER,PrivateSendState(ledger)
        before=[tuple(r) for r in db.execute('SELECT * FROM wechat_send_operations')]
        assert sender.reconcile_existing(ACTION.action_id)['local_echo']
        assert sender.reconcile_existing(ACTION.action_id)['local_echo']
        assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM delivery_evidence').fetchone()[0]==1
        assert [tuple(r) for r in db.execute('SELECT * FROM wechat_send_operations')]==before
        db.execute("UPDATE outbox SET state='SENDING' WHERE action_id=?",(ACTION.action_id,))
        db.execute("UPDATE wechat_send_operations SET worker_reaped=NULL")
        with pytest.raises(SocialError,match="private_prior_worker_cleanup_required"):
            sender.state.begin_private(PEER,ACTION,{'shard':Cursor(0,0)})
