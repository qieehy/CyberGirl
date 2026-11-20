"""Optional selected-peer media resolver. No raw media is stored in the ledger."""

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import asdict

from ..bridges.wechat.send_process import KillJob, python_worker_command
from ..social.models import SocialError
from .native import native_source_root


class PrivateMediaResolver:
    def __init__(self, ledger, receiver):
        self.ledger, self.receiver = ledger, receiver
        with ledger._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workbench_media_results(
                message_id TEXT PRIMARY KEY REFERENCES bridge_records(message_id),
                kind TEXT NOT NULL,state TEXT NOT NULL,text TEXT NOT NULL,error TEXT,
                updated_at REAL NOT NULL)""")

    async def resolve(self, draft_id):
        binding = self.receiver.binding
        row = self.ledger._db.execute(
            """SELECT p.message_id,p.message_type,p.occurred_at,b.local_id,b.sort_seq,c.shard
            FROM workbench_drafts d JOIN bridge_message_payloads p ON p.message_id=d.source_message_id
            JOIN bridge_records b USING(message_id) JOIN bridge_cursors c USING(source_key)
            WHERE d.draft_id=? AND d.scope=? AND d.generation=? AND c.db_epoch=?
              AND p.sender_id=? AND p.message_type IN ('image','voice','sticker')""",
            (
                draft_id,
                binding.scope.key(),
                binding.generation,
                binding.account.db_epoch,
                binding.username,
            ),
        ).fetchone()
        if row is None:
            raise SocialError("private_peer_media_required")
        self._save(row, "PROCESSING")
        root = native_source_root()
        request = dict(
            window=asdict(binding.account.window),
            expected=hashlib.sha256(binding.account.account_id.encode()).hexdigest(),
            username=binding.username,
            local_id=row["local_id"],
            shard=row["shard"],
            occurred_at=row["occurred_at"],
            sort_seq=row["sort_seq"],
            kind=row["message_type"],
            speech_model=str(root / ".state/models/whisper-base"),
        )
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = job = None
        try:
            process = await asyncio.create_subprocess_exec(
                *python_worker_command(root / "scripts/wechat_private_media_worker.py"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                **kwargs,
            )
            if os.name == "nt":
                job = KillJob(process.pid, memory_limit=1024 * 1024 * 1024)
            async with asyncio.timeout(45):
                output, _ = await process.communicate(json.dumps(request).encode() + b"\n")
            if len(output) > 3_000_000:
                raise SocialError("media_size_limit")
            result = json.loads(output)
            if process.returncode or "error" in result:
                code = result.get("error", "media_decode_failed")
                if (
                    not isinstance(code, str)
                    or not code.replace("_", "").isalnum()
                    or len(code) > 100
                ):
                    code = "media_decode_failed"
                raise SocialError(code)
            if (
                result.get("kind") != row["message_type"]
                or not isinstance(result.get("text"), str)
                or not 1 <= len(result["text"]) <= 4100
                or set(result) - {"kind", "text", "image_url"}
            ):
                raise SocialError("media_response_invalid")
            self._save(row, "READY", result["text"])
            return result
        except asyncio.CancelledError:
            self._save(row, "FAILED", error="media_cancelled")
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, SocialError) else "media_decode_failed"
            self._save(row, "FAILED", error=code)
            raise SocialError(code) from None
        finally:
            if process is not None and process.returncode is None:
                if job:
                    job.terminate()
                else:
                    process.kill()
                await process.wait()
            if job:
                job.close()

    def _save(self, row, state, text="", error=None):
        with self.ledger._transaction() as db:
            db.execute(
                """INSERT INTO workbench_media_results VALUES(?,?,?,?,?,?)
                ON CONFLICT(message_id) DO UPDATE SET state=excluded.state,text=excluded.text,
                error=excluded.error,updated_at=excluded.updated_at""",
                (row["message_id"], row["message_type"], state, text, error, self.ledger.now()),
            )
