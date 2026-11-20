"""Private bounded reader pipes. Only the parent has a Ledger connection."""

import asyncio
import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict

from ...social.models import SocialError
from .contracts import Cursor, SourceRecord
from .receive_state import ReaderCatalog
from .send_process import KillJob, unique_object

MAX_FRAME = 1024 * 1024
MAX_PAGES = 100


def encode_frame(value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise SocialError("reader_frame_too_large")
    return data


def decode_frame(raw):
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise SocialError("invalid_reader_frame")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError):
        raise SocialError("invalid_reader_frame") from None
    if type(result) is not dict:
        raise SocialError("invalid_reader_frame")
    return result


class JsonReaderWorker:
    def __init__(self, command, *, timeout=60):
        self.command, self.timeout = command, timeout
        self.process = self.job = None
        self.run_id, self.sequence = secrets.token_hex(16), 0

    async def start(self, window, expected):
        if self.process is not None:
            raise SocialError("reader_already_started")
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(*self.command,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME, **kwargs))
        cancelled = False
        while not spawn.done():
            try:
                await asyncio.shield(spawn)
            except asyncio.CancelledError:
                cancelled = True
        self.process = spawn.result()
        try:
            if os.name == "nt":
                self.job = KillJob(self.process.pid, memory_limit=1024 * 1024 * 1024)
            if cancelled:
                raise asyncio.CancelledError
            return await self.rpc("init", window=asdict(window), expected=expected)
        except BaseException:
            await self.stop()
            raise

    async def rpc(self, command, **payload):
        self.sequence += 1
        request = {"version": 1, "run_id": self.run_id, "sequence": self.sequence,
                   "command": command, **payload}
        try:
            async with asyncio.timeout(self.timeout):
                self.process.stdin.write(encode_frame(request))
                await self.process.stdin.drain()
                raw = await self.process.stdout.readuntil(b"\n")
            response = decode_frame(raw)
        except (TimeoutError, OSError, ValueError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            raise SocialError("reader_transport_failed") from None
        if (set(response) != {"run_id", "sequence", "result"} or response["run_id"] != self.run_id
                or type(response["sequence"]) is not int or response["sequence"] != self.sequence
                or type(response["result"]) is not dict):
            raise SocialError("reader_response_mismatch")
        result = response["result"]
        if set(result) == {"rejected_reason"}:
            reason = result["rejected_reason"]
            if isinstance(reason, str) and re.fullmatch(r"[a-z_0-9]{1,128}", reason):
                raise SocialError(reason)
            raise SocialError("invalid_reader_response")
        return result

    async def stop(self):
        if self.process is None:
            return True
        if self.process.stdin:
            self.process.stdin.close()
        job = self.job
        try:
            if job:
                job.terminate()
            elif self.process.returncode is None:
                self.process.kill()
            async with asyncio.timeout(2):
                await self.process.wait()
                while job and job.active():  # noqa: ASYNC110 - Windows Job has no async notification.
                    await asyncio.sleep(0.01)
            return self.process.returncode is not None
        except TimeoutError:
            return False
        finally:
            if job:
                job.close()
                self.job = None


class ReaderSession:
    def __init__(self, ledger, worker, window, expected, *, on_committed=None, mode="observe"):
        self.catalog, self.worker = ReaderCatalog(ledger), worker
        self.window, self.expected = window, expected
        self.binding = None
        self.on_committed = on_committed
        if mode not in {"observe", "draft"}:
            raise SocialError("invalid_bridge_binding")
        self.mode = mode
        self.rpc_lock = asyncio.Lock()

    async def start(self, *, initialize=False):
        try:
            manifest = await self.worker.start(self.window, self.expected)
            self.binding = self.catalog.attach(manifest, self.window, self.expected, initialize=initialize, mode=self.mode)
            return self.catalog.checkpoints()
        except BaseException:
            await self.close()
            raise

    async def poll(self):
        async with self.rpc_lock:
            return await self._poll()

    def validate_page_records(self, records):
        """Optional stricter target-specific parent validation before any commit or ACK."""

    async def _poll(self):
        try:
            manifest = await self.worker.rpc("manifest")
            self.binding = self.catalog.attach(manifest, self.window, self.expected, mode=self.mode)
            fresh = replay = pages = 0
            for name in self.catalog.checkpoints():
                while True:
                    checkpoint = self.catalog.checkpoints()[name]
                    result = await self.worker.rpc("page", epoch=self.binding.account.db_epoch,
                                                   name=name, checkpoint=checkpoint)
                    if set(result) != {"records"} or type(result["records"]) is not list or len(result["records"]) > 32:
                        raise SocialError("invalid_reader_page")
                    records = []
                    for item in result["records"]:
                        item = dict(item)
                        item["mentioned_ids"] = tuple(item.get("mentioned_ids", ()))
                        records.append(SourceRecord(**item))
                    if not records:
                        break
                    self.validate_page_records(records)
                    accepted = self.catalog.store.ingest(self.binding, checkpoint["shard"],
                        Cursor(**checkpoint["start"]), tuple(records))
                    if self.on_committed is not None:
                        self.on_committed(self, checkpoint, tuple(records), accepted)
                    fresh += sum(r["fresh"] for r in accepted["records"])
                    replay += sum(not r["fresh"] for r in accepted["records"])
                    # Only the completed parent transaction permits ACK.
                    ack = await self.worker.rpc("ack", name=name, cursor=asdict(accepted["cursor"]))
                    if ack != {"accepted": True}:
                        raise SocialError("invalid_reader_ack")
                    pages += 1
                    if pages >= MAX_PAGES:
                        return {"fresh": fresh, "replay": replay, "pages": pages, "more": True}
                    if len(records) < 32:
                        break
            return {"fresh": fresh, "replay": replay, "pages": pages, "more": False}
        except BaseException:
            await self.close()
            raise

    async def close(self):
        stopped = await self.worker.stop()
        if self.binding is not None:
            try:
                self.catalog.store.unbind(self.binding)
            except SocialError as exc:
                if exc.code != "inactive_bridge_binding":
                    raise
            self.binding = None
        if not stopped:
            raise SocialError("reader_stop_unverified")
        return True
