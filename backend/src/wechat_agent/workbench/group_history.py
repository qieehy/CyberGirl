"""Offline history cache and one-shot isolated refresh, independent of model/sender."""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from ..bridges.wechat.send_process import KillJob, python_worker_command, unique_object
from ..social.models import SocialError


def validate_request(group, limit, refresh):
    if (
        not isinstance(group, str)
        or not 1 <= len(group.strip()) <= 128
        or group != group.strip()
        or any(ord(c) < 32 for c in group)
        or type(limit) is not int
        or not 1 <= limit <= 200
        or type(refresh) is not bool
    ):
        raise SocialError("invalid_group_history_request")


class GroupHistory:
    def __init__(self, directory, fingerprint, loader=None):
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise SocialError("invalid_account_fingerprint")
        self.directory = Path(directory) / fingerprint
        self.fingerprint, self.loader = fingerprint, loader
        self.lock = asyncio.Lock()

    def path(self, group):
        return self.directory / (hashlib.sha256(group.encode()).hexdigest() + ".json")

    def save(self, data):
        group = data["group"]
        validate_request(group, 200, False)
        if (
            data.get("account_fingerprint") != self.fingerprint
            or not isinstance(data.get("messages"), list)
            or len(data["messages"]) > 200
            or not re.fullmatch(r"[a-f0-9]{64}", data.get("group_id_sha256", ""))
        ):
            raise SocialError("group_history_scope_mismatch")
        record = dict(data, cached_at=time.time())
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path(group).with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path(group))
        return record

    def import_export(self, path):
        path = Path(path)
        if path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not self.path(data["group"]).exists():
                self.save(dict(data, account_fingerprint=self.fingerprint))

    async def read(self, group, limit=200, refresh=False):
        validate_request(group, limit, refresh)
        async with self.lock:
            record = None
            path = self.path(group)
            if path.is_file():
                record = json.loads(
                    path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
                )
                if (
                    record.get("account_fingerprint") != self.fingerprint
                    or record.get("group") != group
                ):
                    raise SocialError("group_history_scope_mismatch")
            error = None
            cached = True
            if refresh:
                try:
                    if self.loader is None:
                        raise SocialError("group_history_unavailable")
                    data = await self.loader(group, limit)
                    if data.get("group") != group:
                        raise SocialError("group_history_scope_mismatch")
                    record = self.save(data)
                    cached = False
                except SocialError as exc:
                    if record is None or exc.code in {
                        "group_history_scope_mismatch",
                        "group_name_ambiguous",
                    }:
                        raise
                    error = exc.code
            if record is None:
                raise SocialError("group_history_not_cached")
            messages = record["messages"][-limit:]
            return dict(
                record,
                messages=messages,
                count=len(messages),
                cached=cached,
                refresh_error=error,
                model_calls=0,
                send_calls=0,
                read_only=True,
            )


async def native_history(root, database, expected, group, limit, *, kind="group", expected_id=None):
    # Reuse the original account/window metadata; no ledger ownership or cursor mutation.
    database = await asyncio.to_thread(Path(database).resolve)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT account_id FROM reader_accounts WHERE fingerprint=?", (expected,)
        ).fetchone()
        if row is None:
            raise SocialError("group_history_account_unavailable")
        binding = db.execute(
            "SELECT payload FROM bridge_bindings WHERE account_id=? ORDER BY active DESC,rowid DESC LIMIT 1",
            (row[0],),
        ).fetchone()
    if binding is None:
        raise SocialError("group_history_account_unavailable")
    window = json.loads(binding[0])["account"]["window"]
    command = python_worker_command(
        Path(root) / "scripts/read_wechat_group_history.py",
        "--pid",
        str(window["pid"]),
        "--hwnd",
        str(window["hwnd"]),
        "--created-filetime",
        str(window["created_filetime"]),
        "--expected",
        expected,
        "--group",
        group,
        "--limit",
        str(limit),
        "--kind",
        kind,
        *(["--expected-id", expected_id] if expected_id else []),
    )
    process = job = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=4 * 1024 * 1024,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if os.name == "nt":
            job = KillJob(process.pid, memory_limit=1024 * 1024 * 1024)
        async with asyncio.timeout(90):
            raw = await process.stdout.readuntil(b"\n")
            await process.wait()
        if len(raw) > 4 * 1024 * 1024 or process.returncode:
            raise SocialError("group_history_read_failed")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if "error" in data:
            raise SocialError(data["error"])
        return data
    except (TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise SocialError("group_history_read_failed") from None
    finally:
        if process and process.returncode is None:
            if job:
                job.terminate()
            else:
                process.kill()
            await process.wait()
        if job:
            job.close()


def history_for_workspace(root, database):
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT fingerprint FROM reader_accounts").fetchall()
    if len(rows) != 1:
        raise SocialError("group_history_account_unavailable")
    expected = rows[0][0]

    async def load(group, limit):
        return await native_history(root, database, expected, group, limit)

    history = GroupHistory(Path(database).parent / "history", expected, load)
    async def conversation_load(kind, name, expected_id):
        return await native_history(root, database, expected, name, 200,
                                    kind=kind, expected_id=expected_id)

    history.conversation_loader = conversation_load
    history.import_export(Path(root) / "exports/KG牛牛俱乐部-最近200条-20261117.json")
    return history
