"""Three acceptance scenarios: migration, version fencing, and actual loopback API."""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from tests.test_social import Clock, RecordingModel
from wechat_agent.console import SCOPE, ConsoleServer
from wechat_agent.social.decision import RuntimeDecider, SyntheticModel
from wechat_agent.social.ledger import APPLICATION_ID, SCHEMA, Ledger
from wechat_agent.social.models import IncomingEvent, SocialError
from wechat_agent.social.persona import Persona, assemble
from wechat_agent.social.sandbox import FakeAdapter
from wechat_agent.social.service import SocialService

SCRATCH = Path(__file__).resolve().parents[1] / ".build" / "console-tests"


class ConsoleAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "ledger.sqlite3"
        self.clock = Clock()

    def event(self, message_id):
        return IncomingEvent(SCOPE, "sandbox-owner", message_id, self.clock(), self.clock(), "hello", source_verified=True)

    async def test_v1_migration_keeps_legacy_facts_backs_up_and_persists_persona_versions(self):
        db = sqlite3.connect(self.path)
        db.executescript(SCHEMA + f"\nPRAGMA application_id={APPLICATION_ID};")
        db.execute("INSERT INTO scopes VALUES(?,?,1,1,1)", (SCOPE.key(), "sandbox-owner"))
        event = self.event("legacy")
        db.execute("""INSERT INTO inbox(event_id,scope,message_id,fingerprint,text,sender_id,occurred_at,
                      source_verified,is_self,mention_self,received_at,policy_revision,global_revision,context_version,state)
                      VALUES(?,?,?,?,?,?,?,1,0,0,?,1,1,1,'DECIDED')""",
                   (event.key, SCOPE.key(), event.message_id, event.fingerprint, event.text, event.sender_id,
                    event.occurred_at, event.received_at))
        db.execute("INSERT INTO decisions VALUES(?,'SILENT')", (event.key,))
        db.commit()
        db.close()
        with Ledger(self.path, now=self.clock) as ledger:
            snapshot = ledger.console_snapshot(SCOPE)
            self.assertEqual(snapshot["persona"]["version"], 1)
            self.assertEqual(snapshot["events"][0]["persona_version"], None)
            self.assertEqual(ledger.summary(event.key)["state"], "SILENT")
            ledger.save_persona(1, "小麦", "简洁", "合成偏好")
        backups = list(self.path.parent.glob("*.v1-*.bak"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 1)
        backup.close()
        with Ledger(self.path, now=self.clock) as reopened:
            self.assertEqual(reopened.current_persona().name, "小麦")
            self.assertEqual(reopened.current_persona().version, 2)
        self.assertEqual(len(list(self.path.parent.glob("*.v1-*.bak"))), 1)

    async def test_persona_conflict_and_update_fence_old_drafts_and_preserve_replay_inputs(self):
        with Ledger(self.path, now=self.clock) as ledger:
            ledger.configure_owner(SCOPE, "sandbox-owner")
            model = RecordingModel()
            async with SocialService(ledger, RuntimeDecider(model), [FakeAdapter()]) as service:
                event = self.event("old")
                queued = await service.prepare(event)
                row = ledger.console_snapshot(SCOPE)["events"][0]
                _, _, digest = assemble(Persona(), event.text, [])
                self.assertEqual(row["prompt_sha256"], digest)
                changed = ledger.save_persona(1, "小麦", "清楚简洁", "只作表达偏好")
                self.assertEqual(changed.version, 2)
                with self.assertRaises(SocialError) as conflict:
                    ledger.save_persona(1, "旧窗口", "过时", "")
                self.assertEqual(conflict.exception.code, "version_conflict")
                self.assertEqual((await service.dispatch(queued["action_id"]))["state"], "CANCELLED")
                model.entered.clear()
                model.release.clear()
                pending = asyncio.create_task(service.prepare(self.event("pending")))
                await asyncio.wait_for(model.entered.wait(), 2)
                ledger.save_persona(2, "小叶", "自然", "")
                model.release.set()
                self.assertEqual((await pending)["state"], "CANCELLED")
                latest = await service.prepare(self.event("latest"))
                latest_row = ledger.console_snapshot(SCOPE)["events"][0]
                self.assertEqual(latest_row["persona_version"], 3)
                self.assertIn("小叶", latest_row["reply_text"])
                self.assertEqual(latest["state"], "QUEUED")
                old = ledger.console_snapshot(SCOPE)["events"][-1]
                self.assertEqual((old["persona_version"], old["prompt_sha256"]), (1, digest))

    async def test_http_requires_local_authenticated_origin_and_runs_edit_preview_send_replay(self):
        with Ledger(self.path, now=self.clock) as ledger:
            ledger.configure_owner(SCOPE, "sandbox-owner")
            model, adapter = SyntheticModel(), FakeAdapter()
            async with SocialService(ledger, RuntimeDecider(model), [adapter]) as service:
                server = ConsoleServer(ledger, service, token="synthetic-console-token")
                await server.start(0)
                try:
                    async with httpx.AsyncClient(base_url=server.origin, trust_env=False) as client:
                        page = await client.get("/")
                        self.assertEqual(page.status_code, 200)
                        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
                        self.assertEqual((await client.get("/api/state")).status_code, 401)
                        client.headers.update({"Authorization": "Bearer synthetic-console-token", "Origin": server.origin})
                        denied = await client.post("/api/persona", headers={"Origin": "https://unrelated.invalid"}, json={})
                        self.assertEqual(denied.status_code, 403)
                        self.assertNotIn("access-control-allow-origin", denied.headers)
                        self.assertEqual((await client.get("/api/state", headers={"Host": "unrelated.invalid"})).status_code, 403)
                        payload = {"expected_version": 1, "name": "小麦", "style": "清楚简洁", "notes": "<script>fixture</script>"}
                        self.assertEqual((await client.post("/api/persona", json=payload)).status_code, 200)
                        self.assertEqual((await client.post("/api/persona", json=payload)).status_code, 409)
                        message = {"message_id": "http-event", "occurred_at": self.clock(), "text": "hello"}
                        self.assertEqual((await client.post("/api/messages", json=dict(message, target="unknown"))).status_code, 400)
                        result = (await client.post("/api/messages", json=message)).json()
                        self.assertEqual(result["state"], "QUEUED")
                        self.assertEqual((await client.post("/api/messages", json=message)).json(), result)
                        self.assertEqual(model.calls, 1)
                        sent = (await client.post("/api/dispatch", json={"action_id": result["action_id"]})).json()
                        self.assertEqual(sent["state"], "CONFIRMED")
                        state = (await client.get("/api/state")).json()
                        event_id = state["events"][0]["event_id"]
                        replay = (await client.post("/api/replay", json={"event_id": event_id})).json()
                        self.assertEqual(replay["state"], "CONFIRMED")
                        self.assertEqual((model.calls, len(adapter.sent)), (1, 1))
                        self.assertEqual(state["events"][0]["persona_version"], 2)
                        self.assertIn("小麦", state["events"][0]["reply_text"])
                        self.assertNotIn("synthetic-console-token", json.dumps(state))
                finally:
                    await server.aclose()
