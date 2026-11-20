"""Trusted reader child: no ledger path, no log bodies, one unacked page."""

import re
import sys
import time
from dataclasses import asdict
from functools import partial

from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.receive_process import MAX_FRAME, decode_frame, encode_frame
from wechat_agent.social.models import SocialError


class SnapshotReadAttempts:
    """Retry only a discarded read snapshot; never ACK, UI or delivery effects."""
    def __init__(self, *, sleep=time.sleep):
        self.sleep, self.retries, self.exhausted = sleep, 0, 0

    def read(self, operation):
        for attempt in range(3):
            try:
                return operation()
            except SocialError as exc:
                if exc.code != "source_changed_during_snapshot":
                    raise
                if attempt == 2:
                    self.exhausted += 1
                    raise
                self.retries += 1
                self.sleep(0.03)

    def metrics(self):
        return dict(snapshot_retries=self.retries, snapshot_retry_exhausted=self.exhausted)


def serve(factory):
    source = None
    run_id, sequence, pending = None, 0, None
    snapshots = SnapshotReadAttempts()
    try:
        while raw := sys.stdin.buffer.readline(MAX_FRAME + 1):
            request = decode_frame(raw)
            if (request.get("version") != 1 or type(request.get("sequence")) is not int
                    or request["sequence"] != sequence + 1):
                raise SocialError("invalid_reader_request")
            if run_id is None:
                run_id = request.get("run_id")
                if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
                    raise SocialError("invalid_reader_request")
            if request.get("run_id") != run_id:
                raise SocialError("invalid_reader_request")
            sequence += 1
            command = request.get("command")
            common = {"version", "run_id", "sequence", "command"}
            try:
                if command == "init" and source is None and set(request) == common | {"window", "expected"}:
                    source = factory(WindowBinding(**request["window"]), request["expected"])
                    result = snapshots.read(source.manifest)
                elif source is None:
                    raise SocialError("reader_init_required")
                elif command == "manifest" and pending is None and set(request) == common:
                    result = snapshots.read(source.manifest)
                elif command == "echo_baseline" and pending is None and set(request) == common:
                    result = snapshots.read(source.echo_baseline)
                elif command == "page" and pending is None and set(request) == common | {"epoch", "name", "checkpoint"}:
                    records = snapshots.read(partial(source.page, request["epoch"], request["name"], request["checkpoint"]))
                    result = {"records": [asdict(r) for r in records]}
                    if records:
                        pending = (request["name"], asdict(records[-1].position))
                elif command == "read_metrics" and pending is None and set(request) == common:
                    result = snapshots.metrics()
                elif command == "ack" and pending is not None and set(request) == common | {"name", "cursor"}:
                    if (request["name"], request["cursor"]) != pending:
                        raise SocialError("reader_ack_mismatch")
                    pending, result = None, {"accepted": True}
                else:
                    raise SocialError("reader_command_denied")
            except Exception as exc:
                code = exc.code if isinstance(exc, SocialError) else "reader_source_failed"
                if not re.fullmatch(r"[a-z_0-9]{1,128}", code):
                    code = "reader_source_failed"
                result = {"rejected_reason": code}
                sys.stdout.buffer.write(encode_frame({"run_id": run_id, "sequence": sequence, "result": result}))
                sys.stdout.buffer.flush()
                return
            sys.stdout.buffer.write(encode_frame({"run_id": run_id, "sequence": sequence, "result": result}))
            sys.stdout.buffer.flush()
    finally:
        if source is not None:
            source.close()


if __name__ == "__main__":
    from wechat_reader_source import ReaderSource

    try:
        serve(ReaderSource)
    except BaseException:
        # Malformed IPC / disconnect: terminate without traceback or secret data.
        sys.exit(2)
