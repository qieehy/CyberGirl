"""Explicit one-request H4 probes. Records never contain prompts, answers or credentials."""

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from .bootstrap import open_live_application
from .context import LOCAL_SCOPE, ChatSettings
from .providers.codex import ErrorCode, ProviderError
from .providers.codex.catalog import fetch_catalog

STAGES = ("provider", "runtime", "restart")
_PERSONA = "你叫小禾，是透明的 AI 助手。请使用自然、简洁的中文回答，每次不超过 60 字。"
_INPUTS = {"provider": "请只回复：连接成功", "runtime": "请用一句简短的中文介绍你自己，并包含你的名字。",
           "restart": "这是一条重启后的连接检查。请只回复：恢复成功"}


def application_fingerprint() -> str:
    root = Path(__file__).parent
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob("*.py"))}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


@asynccontextmanager
async def _probe_application(model, directory, app_factory, progress):
    progress.update(phase="open", model_http_attempts=0, logical_model_calls=0)
    async with app_factory(model, directory, ChatSettings(system_prompt=_PERSONA)) as app:
        try:
            yield app
        finally:
            # Includes a controlled 401 replay even when the operation fails or
            # is cancelled. No prompt, response text or credentials are copied.
            progress["model_http_attempts"] = app.provider.http_attempts


async def run_probe(stage: str, model: str, directory: Path, *, app_factory=open_live_application,
                    progress: dict | None = None) -> dict:
    if stage not in STAGES:
        raise ProviderError(ErrorCode.INPUT)
    progress = progress if progress is not None else {}
    async with _probe_application(model, directory, app_factory, progress) as app:
        initial = app.tokens.status()
        epoch = app.tokens.context_key()
        progress["phase"] = "catalog"
        catalog = await fetch_catalog(await app.tokens.get_session())
        if catalog.context_key != epoch or app.tokens.context_key() != epoch:
            raise ProviderError(ErrorCode.AUTH)
        if not catalog.contains(model):
            raise ProviderError(ErrorCode.INPUT)
        progress.update(phase="model", logical_model_calls=1)
        started = time.monotonic()
        if stage == "provider":
            result = await app.provider([{"role": "system", "content": _PERSONA},
                                         {"role": "user", "content": _INPUTS[stage]}], [], context_key=epoch)
            text, usage, returned_model, request_id = result.message.content, result.usage, result.model, result.request_id
            runtime = None
        else:
            async def accept_probe(draft) -> bool:
                # Acceptance sink only; this does not claim external delivery.
                return True

            outcome = await app.engine.turn(LOCAL_SCOPE, _INPUTS[stage], accept_probe)
            text, usage = outcome.draft.text, outcome.draft.usage
            returned_model, request_id = outcome.draft.model, outcome.draft.request_id
            runtime = {"class": outcome.runtime_class, "status": outcome.status, "steps": outcome.steps,
                       "model_calls": outcome.model_calls, "sink_confirmed": outcome.confirmed}
        progress["phase"] = "acceptance"
        if app.tokens.context_key() != epoch:
            raise ProviderError(ErrorCode.AUTH)
        elapsed = time.monotonic() - started
        marker = {"provider": "连接成功", "runtime": "小禾", "restart": "恢复成功"}[stage]
        checks = {"nonempty_text": isinstance(text, str) and bool(text.strip()),
                  "chinese_text": isinstance(text, str) and any('\u4e00' <= c <= '\u9fff' for c in text),
                  "expected_marker": isinstance(text, str) and marker in text,
                  "short_response": isinstance(text, str) and len(text) <= 200}
        if runtime is not None:
            checks["runtime_finished_once"] = runtime["status"] == "finished" and runtime["model_calls"] == 1
        final = app.tokens.status()
        return {"status": "passed" if all(checks.values()) else "acceptance_failed", "checks": checks,
                "stage": stage, "selected_model": model, "returned_model": returned_model,
                "response_status": "completed", "request_id": request_id, "elapsed_seconds": round(elapsed, 3),
                "usage": asdict(usage) if usage is not None else None, "runtime": runtime,
                "logical_model_calls": 1, "model_http_attempts": app.provider.http_attempts,
                "catalog_request_id": catalog.request_id, "catalog_fetched_at": catalog.fetched_at,
                "login_epoch_fingerprint": epoch,
                "auth_state_at_start": initial["state"], "auth_state_at_end": final["state"],
                "token_expiry_advanced": initial["expires_at"] != final["expires_at"],
                "real_refresh_acceptance": "not_established_by_this_probe",
                "delivery": "local_acceptance_sink", "chat_history_persisted": False}


def _save_record(path: Path, record: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def verify_command(stage: str, model: str, directory: Path, output: Path) -> dict:
    record = {"schema": 2, "stage": stage, "status": "started", "process_id": os.getpid(),
              "phase": "open", "selected_model": model, "model_http_attempts": 0, "logical_model_calls": 0,
              "run_id": uuid.uuid4().hex, "started_at": datetime.now(UTC).isoformat(),
              "application_version": version("wechat-agent"), "framework_version": version("ai-agent"),
              "application_source_sha256": application_fingerprint()}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve evidence before any request. Existing records prevent accidental replay.
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(record, handle)
    except FileExistsError:
        raise ProviderError(ErrorCode.INPUT) from None
    try:
        record.update(await run_probe(stage, model, directory, progress=record))
    except ProviderError as exc:
        record.update(status="failed", error=exc.code.value, http_status=exc.status,
                      request_id=exc.request_id, retry_after=exc.retry_after)
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        raise
    except Exception:
        record.update(status="failed", error="internal_error")
        raise
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()
        _save_record(output, record)
    return record
