"""Explicit single-turn live persona previews; never an inbox/outbox sender."""

import asyncio
import hashlib
import json
import re
from pathlib import Path

from .bootstrap import open_live_application
from .context import LOCAL_SCOPE, ChatSettings
from .providers.codex import ErrorCode, ProviderError
from .providers.codex.catalog import fetch_catalog
from .social.models import Decision, SocialError, identifier, text_value
from .social.persona import assemble


class PreviewService:
    def __init__(self, ledger, model: str, directory: Path, *, app_factory=open_live_application,
                 catalog_fetch=fetch_catalog):
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", model):
            raise SocialError("invalid_model")
        self.ledger, self.model, self.directory = ledger, model, directory
        self.app_factory, self.catalog_fetch = app_factory, catalog_fetch
        self.busy = False

    @staticmethod
    def _summary(row):
        result = json.loads(row["result"])
        result.update(request_id=row["request_id"], state=row["state"], model=row["model"],
                      persona_version=row["persona_version"], started_at=row["started_at"],
                      prompt_sha256=row["prompt_sha256"])
        if row["state"] == "UNKNOWN":
            result.update(error="interrupted_preview", model_http_attempts=None)
        return result

    def recent(self):
        with self.ledger._transaction() as db:
            return [self._summary(r) for r in db.execute("SELECT * FROM previews ORDER BY started_at DESC,rowid DESC LIMIT 10")]

    def _valid(self, persona_version, revision):
        with self.ledger._transaction() as db:
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            return not control["paused"] and control["persona_version"] == persona_version and control["revision"] == revision

    async def run(self, request_id, expected_version, text):
        identifier(request_id)
        text_value(text, 4000)
        if type(expected_version) is not int:
            raise SocialError("invalid_persona")
        fingerprint = hashlib.sha256(json.dumps([expected_version, text, self.model], ensure_ascii=False).encode()).hexdigest()
        with self.ledger._transaction() as db:
            old = db.execute("SELECT * FROM previews WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise SocialError("event_identity_conflict")
                return self._summary(old)
            if self.busy:
                raise SocialError("busy")
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            if control["persona_version"] != expected_version:
                raise SocialError("version_conflict")
            if control["paused"]:
                raise SocialError("policy_denied")
            if db.execute("SELECT COUNT(*) FROM previews").fetchone()[0] >= 500:
                raise SocialError("preview_capacity")
            persona = self.ledger._persona(db, expected_version)
            system, content, digest = assemble(persona, text, [])
            revision = control["revision"]
            db.execute("INSERT INTO previews VALUES(?,?,?,?,?,?,?,'RUNNING',?,?)",
                       (request_id, fingerprint, expected_version, revision, self.model, text, digest,
                        self.ledger.now(), json.dumps({"phase": "open", "model_http_attempts": None})))
        self.busy = True
        result = {"phase": "open", "model_http_attempts": 0, "logical_model_calls": 0}
        state = "FAILED"
        try:
            async with asyncio.timeout(110), self.app_factory(
                    self.model, self.directory,
                    ChatSettings(system_prompt=system, max_input_chars=20000, context_chars=30000, history_pairs=1)) as app:
                try:
                    epoch = app.tokens.context_key()
                    result["phase"] = "catalog"
                    catalog = await self.catalog_fetch(await app.tokens.get_session())
                    if catalog.context_key != epoch or app.tokens.context_key() != epoch:
                        raise ProviderError(ErrorCode.AUTH)
                    if not catalog.contains(self.model):
                        raise SocialError("model_unavailable")
                    if not self._valid(expected_version, revision):
                        raise SocialError("stale_preview")
                    result.update(phase="model", logical_model_calls=1)

                    async def preview_only(draft):
                        return False

                    outcome = await app.engine.turn(LOCAL_SCOPE, content, preview_only)
                    decision = Decision.parse(outcome.draft.text)
                    if app.tokens.context_key() != epoch:
                        raise ProviderError(ErrorCode.AUTH)
                    if not self._valid(expected_version, revision):
                        raise SocialError("stale_preview")
                    result.update(phase="complete", action=decision.action.value, reply_text=decision.reply_text,
                                  runtime_class=outcome.runtime_class, returned_model=outcome.draft.model)
                    state = "COMPLETED"
                finally:
                    result["model_http_attempts"] = app.provider.http_attempts
        except asyncio.CancelledError:
            state = "UNKNOWN"
            result["error"] = "interrupted_preview"
            raise
        except TimeoutError:
            state = "FAILED"
            result["error"] = "timeout"
        except (ProviderError, SocialError) as exc:
            state = "FAILED"
            result["error"] = str(exc.code)
            if str(exc.code) == "stale_preview":
                state = "SUPERSEDED"
        except Exception:
            state = "FAILED"
            result["error"] = "internal_error"
        finally:
            self.busy = False
            if state == "COMPLETED" and not self._valid(expected_version, revision):
                state = "SUPERSEDED"
                result["error"] = "stale_preview"
            if state != "COMPLETED":
                result.pop("reply_text", None)
                result.pop("action", None)
            result["finished_at"] = self.ledger.now()
            with self.ledger._transaction() as db:
                db.execute("UPDATE previews SET state=?,result=? WHERE request_id=?",
                           (state, json.dumps(result, ensure_ascii=False), request_id))
        with self.ledger._transaction() as db:
            return self._summary(db.execute("SELECT * FROM previews WHERE request_id=?", (request_id,)).fetchone())
