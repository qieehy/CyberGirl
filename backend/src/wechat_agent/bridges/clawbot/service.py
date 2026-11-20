"""Owner-only, window-independent receive/model/send loop with durable attempts."""

import asyncio
import base64
import io
import json
import re

from ...providers.codex import ProviderError
from ...social.models import ActionKind, SocialError
from ...workbench.models import require_model, save_model, selected_model
from ...workbench.preferences import match_rule, rule_decision
from ..wechat.background_text import require_background_bmp_text
from ..wechat.contracts import digest
from .client import ORIGIN, ClawBotError, approved_origin
from .store import ClawBotPreferences, binding_for, ingest, initialize


class AutoReply:
    def __init__(self, service):
        self.service = service
        self.enabled = False
        self.epoch = self.floor = 0
        self.started_at = 0
        self.phase = "stopped"
        self.last_error = None

    def snapshot(self):
        row = self.service.ledger._db.execute(
            "SELECT state,reason FROM oc_runs WHERE scope=? ORDER BY created_at DESC LIMIT 1",
            (self.service.binding.scope.key(),),
        ).fetchone()
        return dict(
            enabled=self.enabled,
            phase=self.phase,
            last_error=self.last_error,
            requires_consent=not self.service.authorized(),
            last_delivery=dict(row) if row else None,
        )

    def set_enabled(self, enabled, *, consent=False):
        if type(enabled) is not bool or type(consent) is not bool:
            raise SocialError("invalid_request")
        s = self.service
        if enabled:
            if not s.logged_in or not s.reading or s.closed:
                raise SocialError("clawbot_login_required")
            if not s.authorized():
                if not consent:
                    raise SocialError("clawbot_consent_required")
                with s.ledger._transaction() as db:
                    db.execute(
                        "INSERT INTO oc_authorization VALUES(?,?,?)",
                        (
                            s.binding.scope.key(),
                            s.ledger.now(),
                            "owner_only:new_text_and_cloud_voice_transcript:context<=16:generations<=30",
                        ),
                    )
            if self.enabled:
                return self.snapshot()
            self.floor = s.ledger._db.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM oc_messages WHERE scope=?",
                (s.binding.scope.key(),),
            ).fetchone()[0]
            self.started_at = s.ledger.now()
            self.last_error = None
        self.epoch += 1
        self.enabled = enabled
        self.phase = "waiting" if enabled else "stopped"
        return self.snapshot()


class ClawBotService:
    def __init__(self, ledger, client, vault, generator, *, bootstrap=None, resume=False):
        self.ledger, self.client, self.vault, self.generator = ledger, client, vault, generator
        initialize(ledger)
        self.model = generator.model
        self.closed = self.logged_in = self.reading = False
        self.read_error = None
        self.operation_lock = asyncio.Lock()
        self.poll_task = self.reply_task = self.login_task = None
        self.login = dict(
            status="not_logged_in", qr_png=None, expires_at=None, need_pin=False, error=None
        )
        self._qrcode = self._pin = None
        self._login_origin = ORIGIN
        self.resume = resume
        self._bind("not-logged-in", "unpaired")
        self.auto_reply = AutoReply(self)
        if bootstrap and self.preferences.snapshot()["revision"] == 0:
            p = bootstrap["persona"]
            current = ledger.current_persona()
            if current.name == "小禾" and current.version == 1:
                ledger.save_persona(current.version, p["name"], p["style"], p["notes"])
            self.preferences.save(0, config=bootstrap["config"], rules=bootstrap["rules"])

    def _bind(self, bot_id, owner_id):
        previous = self.preferences.snapshot() if hasattr(self, "preferences") else None
        self.binding = binding_for(self.ledger, bot_id, owner_id)
        self.preferences = ClawBotPreferences(self.ledger, self.binding)
        if previous and self.preferences.snapshot()["revision"] == 0:
            self.preferences.save(0, config=previous["config"], rules=previous["rules"])
        self.model = self.generator.model = selected_model(
            self.ledger, self.binding.scope.key(), self.model
        )

    def authorized(self):
        return bool(
            self.ledger._db.execute(
                "SELECT 1 FROM oc_authorization WHERE scope=?", (self.binding.scope.key(),)
            ).fetchone()
        )

    def _open(self):
        if self.closed:
            raise SocialError("workbench_not_reading")

    async def start(self):
        record = await asyncio.to_thread(self.vault.load)
        if record:
            self.client.token, self.client.origin = record["token"], record["origin"]
            self._bind(record["bot_id"], record["owner_id"])
            self.logged_in = True
            self.login.update(status="confirmed")
            self._start_poll()
            if self.resume and self.authorized():
                self.auto_reply.set_enabled(True)

    async def start_login(self):
        self._open()
        if self.logged_in:
            raise SocialError("clawbot_already_logged_in")
        if self.login_task and not self.login_task.done():
            self.login_task.cancel()
            await asyncio.gather(self.login_task, return_exceptions=True)
        data = await self.client.login_qr()
        qr, content = data.get("qrcode"), data.get("qrcode_img_content")
        if (
            not isinstance(qr, str)
            or not 1 <= len(qr) <= 8192
            or not isinstance(content, str)
            or not 1 <= len(content) <= 8192
        ):
            raise SocialError("clawbot_qr_invalid")
        import qrcode

        image = qrcode.make(content)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self._qrcode, self._pin, self._login_origin = qr, None, ORIGIN
        self.login = dict(
            status="wait",
            qr_png="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
            expires_at=self.ledger.now() + 300,
            need_pin=False,
            error=None,
        )
        self.login_task = asyncio.create_task(self._login_poll())
        return self.login_snapshot()

    def login_snapshot(self):
        return dict(
            self.login,
            logged_in=self.logged_in,
            bot_id=self.binding.account.account_id if self.logged_in else None,
            owner_id=self.binding.username if self.logged_in else None,
        )

    def submit_pin(self, pin):
        if (
            not self.login["need_pin"]
            or not isinstance(pin, str)
            or not re.fullmatch(r"\d{4,8}", pin)
        ):
            raise SocialError("clawbot_pin_invalid")
        self._pin = pin
        self.login["need_pin"] = False
        return dict(accepted=True)

    async def _login_poll(self):
        try:
            while not self.closed and self.ledger.now() < self.login["expires_at"]:
                try:
                    data = await self.client.login_status(
                        self._qrcode, origin=self._login_origin, pin=self._pin
                    )
                except ClawBotError as exc:
                    self.login["error"] = exc.code
                    await asyncio.sleep(3)
                    continue
                status = data.get("status", "wait")
                self.login.update(status=status, error=None)
                if "need_verifycode" in status:
                    self._pin = None
                    self.login["need_pin"] = True
                    await asyncio.sleep(2)
                    continue
                if status == "scaned":
                    self._pin = None
                if status == "scaned_but_redirect" and data.get("redirect_host"):
                    self._login_origin = approved_origin("https://" + data["redirect_host"])
                if status in {"expired", "verify_code_blocked", "binded_redirect"}:
                    return
                if status == "confirmed":
                    token, bot, owner = (
                        data.get("bot_token"),
                        data.get("ilink_bot_id"),
                        data.get("ilink_user_id"),
                    )
                    if (
                        not isinstance(token, str)
                        or not 1 <= len(token) <= 8192
                        or any(
                            not isinstance(v, str) or not 1 <= len(v) <= 128 for v in (bot, owner)
                        )
                    ):
                        raise SocialError("clawbot_login_identity_missing")
                    origin = approved_origin(data.get("baseurl") or self._login_origin)
                    await asyncio.to_thread(self.vault.save, token, bot, owner, origin)
                    self.client.token, self.client.origin = token, origin
                    self._bind(bot, owner)
                    self.logged_in = True
                    self.read_error = None
                    self.login.update(qr_png=None, need_pin=False)
                    self._qrcode = self._pin = None
                    self._start_poll()
                    if self.resume and self.authorized():
                        self.auto_reply.set_enabled(True)
                    return
                await asyncio.sleep(2)
            self.login["status"] = "expired"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.login["error"] = (
                exc.code if isinstance(exc, SocialError) else "clawbot_login_failed"
            )
            self.login["status"] = "failed"

    def _start_poll(self):
        self.reading = True
        self.poll_task = asyncio.create_task(self._poll())
        self.reply_task = asyncio.create_task(self._replies())

    async def _poll(self):
        delay = 2
        try:
            while self.logged_in and not self.closed:
                row = self.ledger._db.execute(
                    "SELECT value FROM oc_cursor WHERE scope=?", (self.binding.scope.key(),)
                ).fetchone()
                cursor = self.vault.open(row[0]) if row else ""
                try:
                    data = await self.client.updates(cursor)
                    ingest(self.ledger, self.vault, self.binding, data)
                    self.read_error = None
                    delay = 2
                    await asyncio.sleep(0.1)
                except (ClawBotError, SocialError) as exc:
                    self.read_error = exc.code
                    if exc.code == "clawbot_session_expired":
                        self.auto_reply.set_enabled(False)
                        self.logged_in = False
                        self.client.token = None
                        self.login.update(status="expired", error=exc.code)
                        break
                    if exc.code in {
                        "clawbot_message_conflict",
                        "clawbot_storage_limit",
                        "clawbot_response_invalid",
                    }:
                        self.auto_reply.set_enabled(False)
                        self.auto_reply.last_error = exc.code
                        break
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.read_error = "clawbot_receive_failed"
            self.auto_reply.set_enabled(False)
        finally:
            self.reading = False

    async def _replies(self):
        while not self.closed and self.logged_in:
            if self.auto_reply.enabled:
                now = self.ledger.now()
                row = self.ledger._db.execute(
                    """SELECT rowid AS position,* FROM oc_messages m
                    WHERE scope=? AND rowid>? AND kind IN ('text','voice') AND occurred_at>=?
                    AND occurred_at>=? AND NOT EXISTS(SELECT 1 FROM oc_runs r WHERE r.source=m.message_id)
                    ORDER BY rowid DESC LIMIT 1""",
                    (
                        self.binding.scope.key(),
                        self.auto_reply.floor,
                        int(self.auto_reply.started_at),
                        now - 120,
                    ),
                ).fetchone()
                if row:
                    self.auto_reply.floor = row["position"]
                    await self.reply(row)
            await asyncio.sleep(0.3)

    def _update_run(self, run_id, **values):
        allowed = {"state", "text", "reason", "trace", "receipt"}
        if not values or not set(values) <= allowed:
            raise SocialError("invalid_request")
        with self.ledger._transaction() as db:
            db.execute(
                "UPDATE oc_runs SET " + ",".join(k + "=?" for k in values) + " WHERE draft_id=?",
                (*values.values(), run_id),
            )

    async def reply(self, source):
        epoch, settings, persona = (
            self.auto_reply.epoch,
            self.preferences.snapshot(),
            self.ledger.current_persona(),
        )
        rule = match_rule(settings["rules"], source["text"])
        if source["kind"] == "voice" and not settings["config"]["voice_enabled"]:
            return
        now = self.ledger.now()
        midnight = now - (now + 8 * 3600) % 86400
        used = self.ledger._db.execute(
            "SELECT COUNT(*) FROM oc_runs WHERE created_at>=? AND model_calls=1", (midnight,)
        ).fetchone()[0]
        if not rule and used >= settings["config"]["daily_budget"]:
            self.auto_reply.last_error = "model_daily_budget"
            self.auto_reply.enabled = False
            return
        run_id = digest(["clawbot-run", source["message_id"]])
        with self.ledger._transaction() as db:
            if db.execute(
                "SELECT 1 FROM oc_runs WHERE source=?", (source["message_id"],)
            ).fetchone():
                return
            db.execute(
                """INSERT INTO oc_runs(draft_id,source,scope,model,model_calls,state,text,client_id,created_at)
                VALUES(?,?,?,?,?,'GENERATING','',?,?)""",
                (
                    run_id,
                    source["message_id"],
                    self.binding.scope.key(),
                    "local-rule" if rule else self.model,
                    0 if rule else 1,
                    "cg-" + run_id[:32],
                    now,
                ),
            )
        self.auto_reply.phase = "generating"
        try:
            if rule:
                decision, trace = (
                    rule_decision(rule),
                    dict(name=rule["name"], rule_id=rule["id"], inference_calls=0),
                )
            else:
                rows = self.ledger._db.execute(
                    """SELECT m.text AS user,r.text AS assistant FROM oc_runs r
                    JOIN oc_messages m ON m.message_id=r.source WHERE r.scope=? AND r.state='API_ACCEPTED'
                    ORDER BY r.created_at DESC LIMIT ?""",
                    (self.binding.scope.key(), settings["config"]["context_pairs"]),
                ).fetchall()
                history, size = [], 0
                for item in rows:
                    size += len(item["user"]) + len(item["assistant"])
                    if size > 10000:
                        break
                    history.append(dict(item))
                async with asyncio.timeout(110):
                    decision, trace = await self.generator.generate(
                        source["text"], list(reversed(history)), persona, settings["config"]
                    )
            self._update_run(run_id, trace=json.dumps(trace))
            async with self.operation_lock:
                latest = self.ledger._db.execute(
                    "SELECT MAX(rowid) FROM oc_messages WHERE scope=?", (self.binding.scope.key(),)
                ).fetchone()[0]
                if (
                    not self.auto_reply.enabled
                    or self.auto_reply.epoch != epoch
                    or not self.logged_in
                    or self.closed
                    or self.preferences.snapshot()["revision"] != settings["revision"]
                    or self.ledger.current_persona().version != persona.version
                    or latest != source["position"]
                ):
                    self._update_run(run_id, state="CANCELLED", reason="context_changed")
                    return
                if decision.action == ActionKind.SILENT:
                    self._update_run(run_id, state="SILENT")
                    return
                require_background_bmp_text(decision.reply_text)
                if len(decision.reply_text) > settings["config"]["reply_max_chars"]:
                    raise SocialError("reply_configured_limit")
                self._update_run(run_id, state="SENDING", text=decision.reply_text)
                self.auto_reply.phase = "sending"
                response = await self.client.send(
                    self.binding.username,
                    decision.reply_text,
                    self.vault.open(source["context"]),
                    "cg-" + run_id[:32],
                )
                self._update_run(
                    run_id,
                    state="API_ACCEPTED",
                    reason="clawbot_api_accepted",
                    receipt=str(response.get("message_id", ""))[:128],
                )
        except asyncio.CancelledError:
            self._update_run(run_id, state="UNKNOWN", reason="interrupted_operation")
            raise
        except Exception as exc:
            uncertain = isinstance(exc, ClawBotError) and exc.uncertain
            code = (
                exc.code
                if isinstance(exc, (SocialError, ProviderError))
                else "clawbot_reply_failed"
            )
            self._update_run(run_id, state="UNKNOWN" if uncertain else "FAILED", reason=str(code))
            self.auto_reply.last_error = str(code)
            self.auto_reply.enabled = False
        finally:
            self.auto_reply.phase = "waiting" if self.auto_reply.enabled else "stopped"

    async def models(self):
        try:
            catalog = await self.generator.catalog()
        except ProviderError as exc:
            raise SocialError(str(exc.code)) from None
        return dict(selected=self.model, models=[m.slug for m in catalog.models])

    async def switch_model(self, model, expected_model):
        require_model(model)
        if expected_model != self.model:
            raise SocialError("version_conflict")
        if model == self.model:
            return dict(model=model, changed=False)
        if model not in (await self.models())["models"]:
            raise SocialError("model_unavailable")
        async with self.operation_lock:
            if expected_model != self.model:
                raise SocialError("version_conflict")
            enabled = self.auto_reply.enabled
            self.auto_reply.set_enabled(False)
            if self.reply_task and not self.reply_task.done():
                self.reply_task.cancel()
                await asyncio.gather(self.reply_task, return_exceptions=True)
            self.model = self.generator.model = model
            save_model(self.ledger, self.binding.scope.key(), model)
            if self.logged_in:
                self.reply_task = asyncio.create_task(self._replies())
            if enabled:
                self.auto_reply.set_enabled(True)
        return dict(model=model, changed=True)

    def snapshot(self):
        scope = self.binding.scope.key()
        messages = [
            dict(
                message_id=r["message_id"],
                text=("[语音转写] " if r["kind"] == "voice" else "") + r["text"],
                message_type=r["kind"],
                sender_id=r["sender_id"],
                occurred_at=r["occurred_at"],
                received_at=r["received_at"],
                disposition="OBSERVED"
                if r["kind"] in ("text", "voice")
                else "unsupported_message_type",
            )
            for r in self.ledger._db.execute(
                "SELECT * FROM oc_messages WHERE scope=? ORDER BY rowid DESC LIMIT 100", (scope,)
            )
        ]
        drafts = []
        for r in self.ledger._db.execute(
            "SELECT * FROM oc_runs WHERE scope=? ORDER BY created_at DESC LIMIT 100", (scope,)
        ):
            state = {
                "GENERATING": "RUNNING",
                "API_ACCEPTED": "SUBMITTED",
                "SENDING": "SUBMITTED",
            }.get(r["state"], r["state"])
            item = dict(
                draft_id=r["draft_id"],
                source_message_id=r["source"],
                model=r["model"],
                state=state,
                reason=r["reason"],
                text=r["text"] or None,
                version=1,
                expires_at=r["created_at"] + 120,
                local_echo=False,
                api_accepted=r["state"] == "API_ACCEPTED",
                send_error=None,
                model_trace=json.loads(r["trace"]) if r["model_calls"] else None,
            )
            if r["state"] in ("API_ACCEPTED", "UNKNOWN", "FAILED"):
                item["delivery"] = dict(
                    state="API_ACCEPTED"
                    if r["state"] == "API_ACCEPTED"
                    else "UNCONFIRMED"
                    if r["state"] == "UNKNOWN"
                    else "FAILED",
                    reason=r["reason"],
                )
            drafts.append(item)
        control = self.ledger._db.execute("SELECT * FROM control").fetchone()
        return dict(
            provider="clawbot",
            mode="clawbot",
            account_id=self.binding.account.account_id,
            target=self.binding.username,
            target_label="扫码者私聊" if self.logged_in else "ClawBot · 等待登录",
            reading=self.reading,
            read_error=self.read_error,
            connected=not self.closed,
            model=self.model,
            generation=self.binding.generation,
            send_enabled=False,
            private_send_enabled=False,
            local_drafts_enabled=False,
            auto_reply_available=self.logged_in and self.reading,
            auto_reply=self.auto_reply.snapshot(),
            media_enabled=True,
            media_capabilities=dict(
                images_enabled=False, stickers_enabled=False, voice_enabled=True
            ),
            preferences_revision=self.preferences.snapshot()["revision"],
            message_revision=len(messages),
            persona_version=control["persona_version"],
            policy_version=control["revision"],
            paused=not self.auto_reply.enabled,
            model_switch_enabled=True,
            model_switching=False,
            blocked_reason=None,
            messages=messages,
            drafts=drafts,
            capabilities=dict(
                protocol=True,
                window_independent=True,
                background=True,
                foreground=True,
                minimized=True,
                groups=False,
            ),
        )

    async def pause(self, expected_version, paused):
        if paused is not True:
            raise SocialError("invalid_request")
        if (
            expected_version
            != self.ledger._db.execute("SELECT revision FROM control").fetchone()[0]
        ):
            raise SocialError("version_conflict")
        self.auto_reply.set_enabled(False)
        return dict(paused=True)

    async def aclose(self):
        self.closed = True
        self.auto_reply.set_enabled(False)
        tasks = [
            t for t in (self.login_task, self.poll_task, self.reply_task) if t and not t.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
