"""Bounded JSON pipes and a Windows kill-on-close job for a trusted UI worker.

No native UI implementation is selected here. The executable is application
configuration. A worker must wait for init before doing anything with the UI.
"""

import asyncio
import ctypes as C
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from ...social.models import SocialError
from .contracts import WindowBinding
from .sender import InputObservation

MAX_FRAME = 16384

_WORKER_BOOTSTRAP = """
import runpy
import site
import sys
from pathlib import Path
packages, environment, script, *arguments = sys.argv[1:]
sys.prefix = sys.exec_prefix = environment
site.addsitedir(packages)
sys.path.insert(0, str(Path(script).resolve().parent))
sys.argv = [script, *arguments]
runpy.run_path(script, run_name="__main__")
"""


def python_worker_command(script, *arguments):
    """Avoid the Windows venv redirector: Job PID must be the actual worker.

    Isolated base Python loads only the current venv's site directory, keeping
    its locked packages/editable imports. Data is argv, never generated code.
    """
    script = Path(script).resolve()
    if not script.is_file() or any(not isinstance(arg, str) for arg in arguments):
        raise ValueError("worker script and arguments must be application-owned")
    if os.name == "nt" and Path(sys.prefix).resolve() != Path(sys.base_prefix).resolve():
        executable = Path(sys._base_executable).resolve()
        packages = Path(sys.prefix).resolve() / "Lib/site-packages"
        if not executable.is_file() or not packages.is_dir():
            raise ValueError("worker interpreter environment unavailable")
        return (str(executable), "-I", "-S", "-c", _WORKER_BOOTSTRAP, str(packages), str(Path(sys.prefix).resolve()),
                str(script), *arguments)
    return (sys.executable, str(script), *arguments)


class KillJob:
    """Only this child and its descendants are assigned, never the Weixin PID."""

    def __init__(self, pid, *, memory_limit=None):
        class Basic(C.Structure):
            _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", C.c_uint32),
                        ("min_working", C.c_size_t), ("max_working", C.c_size_t), ("active", C.c_uint32),
                        ("affinity", C.c_size_t), ("priority", C.c_uint32), ("scheduling", C.c_uint32)]

        class Limits(C.Structure):
            _fields_ = [("basic", Basic), ("io", C.c_uint64 * 6), ("process_memory", C.c_size_t),
                        ("job_memory", C.c_size_t), ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]

        self.k = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([C.c_void_p, C.c_wchar_p], C.c_void_p),
            "SetInformationJobObject": ([C.c_void_p, C.c_int, C.c_void_p, C.c_uint32], C.c_int),
            "OpenProcess": ([C.c_uint32, C.c_int, C.c_uint32], C.c_void_p),
            "AssignProcessToJobObject": ([C.c_void_p, C.c_void_p], C.c_int),
            "CloseHandle": ([C.c_void_p], C.c_int),
            "TerminateJobObject": ([C.c_void_p, C.c_uint32], C.c_int),
            "QueryInformationJobObject": ([C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.c_void_p], C.c_int),
        }
        for name, (args, returns) in signatures.items():
            getattr(self.k, name).argtypes, getattr(self.k, name).restype = args, returns
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        process = None
        try:
            limits = Limits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if memory_limit is not None:
                limits.basic.flags |= 0x0200  # JOB_OBJECT_LIMIT_JOB_MEMORY
                limits.job_memory = memory_limit
            if not self.k.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)):
                raise C.WinError(C.get_last_error())
            process = self.k.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process or not self.k.AssignProcessToJobObject(self.handle, process):
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                self.k.CloseHandle(process)

    def close(self):
        if self.handle:
            if not self.k.CloseHandle(self.handle):
                raise C.WinError(C.get_last_error())
            self.handle = None

    def terminate(self):
        if not self.k.TerminateJobObject(self.handle, 1):
            raise C.WinError(C.get_last_error())

    def active(self):
        accounting = C.create_string_buffer(48)
        if not self.k.QueryInformationJobObject(self.handle, 1, accounting, 48, None):
            raise C.WinError(C.get_last_error())
        return C.c_uint32.from_buffer(accounting, 40).value


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SocialError("duplicate_worker_json_key")
        value[key] = item
    return value


class JsonSendWorker:
    def __init__(self, command):
        if type(command) is not tuple or not command or any(not isinstance(s, str) or not s for s in command):
            raise ValueError("worker command must be a fixed application-owned tuple")
        if (os.name == "nt" and len(command) >= 2 and Path(command[0]).resolve() == Path(sys.executable).resolve()
                and Path(command[1]).suffix == ".py" and Path(command[1]).is_file()):
            command = python_worker_command(command[1], *command[2:])
        self.command, self.process, self.job = command, None, None
        self.operation_id, self.sequence = None, 0
        self.input_used = self.submit_used = False
        self.intent_recorder = self.gesture_result_recorder = None

    def bind_gesture_journal(self, intent_recorder, result_recorder):
        if self.process is not None or self.intent_recorder is not None or not callable(intent_recorder) or not callable(result_recorder):
            raise SocialError("gesture_journal_binding_denied")
        self.intent_recorder, self.gesture_result_recorder = intent_recorder, result_recorder

    async def start(self, operation_id, binding, action):
        if self.process is not None:
            raise SocialError("worker_already_started")
        self.operation_id = operation_id
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
        if os.name == "nt":
            self.job = KillJob(self.process.pid)  # No init/permit before assignment succeeds.
        if cancelled:
            raise asyncio.CancelledError
        await self._rpc("init", binding=asdict(binding), action=asdict(action))

    async def _rpc(self, command, **payload):
        self.sequence += 1
        request = {"version": 1, "operation_id": self.operation_id, "sequence": self.sequence,
                   "command": command, **payload}
        data = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_FRAME:
            raise SocialError("worker_request_too_large")
        self.process.stdin.write(data)
        await self.process.stdin.drain()
        ordinal = 0
        while True:
            raw = await self.process.stdout.readuntil(b"\n")
            if len(raw) > MAX_FRAME:
                raise SocialError("worker_response_too_large")
            response = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            if type(response) is not dict or "intent" not in response:
                break
            from .gesture_protocol import INTENTS, MAX_INTENTS

            intent = response["intent"]
            if (command != "submit_once" or self.intent_recorder is None or ordinal >= MAX_INTENTS
                    or set(response) != {"operation_id", "sequence", "intent"}
                    or response["operation_id"] != self.operation_id or type(response["sequence"]) is not int
                    or response["sequence"] != self.sequence or type(intent) is not dict
                    or set(intent) != {"stage", "ordinal"} or type(intent["stage"]) is not str or intent["stage"] not in INTENTS
                    or type(intent["ordinal"]) is not int or intent["ordinal"] != ordinal + 1):
                raise SocialError("gesture_intent_request_invalid")
            ordinal += 1
            accepted = False
            try:
                self.intent_recorder(intent["stage"])
                accepted = True
            except Exception:
                pass  # Fail closed; child still gets a chance for original-window cleanup.
            ack = {"operation_id": self.operation_id, "sequence": self.sequence,
                   "intent_ack": {**intent, "accepted": accepted}}
            self.process.stdin.write(json.dumps(ack, separators=(",", ":")).encode() + b"\n")
            await self.process.stdin.drain()
        if (type(response) is not dict or set(response) != {"operation_id", "sequence", "result"}
                or response["operation_id"] != self.operation_id or type(response["sequence"]) is not int
                or response["sequence"] != self.sequence or type(response["result"]) is not dict):
            raise SocialError("worker_response_mismatch")
        result = response["result"]
        if (set(result) == {"rejected_reason"} and isinstance(result["rejected_reason"], str)
                and re.fullmatch(r"[a-z_0-9]{1,128}", result["rejected_reason"])):
            raise SocialError(result["rejected_reason"])
        if ordinal and set(result) != {"gesture_result"}:
            raise SocialError("gesture_terminal_result_required")
        return result

    async def inspect(self):
        result = await self._rpc("inspect")
        if set(result) != {"observation"} or type(result["observation"]) is not dict:
            raise SocialError("invalid_worker_observation")
        data = dict(result["observation"])
        data["window"] = WindowBinding(**data["window"])
        return InputObservation(**data)

    async def prepare_input_focus(self):
        if not self._accepted(await self._rpc("focus_once")):
            raise SocialError("worker_focus_unknown")

    async def warmup(self):
        result = await self._rpc("capabilities")
        if not isinstance(result.get("observation"), dict):
            raise SocialError("invalid_preparation_observation")
        data = dict(result["observation"])
        data["window"] = WindowBinding(**data["window"])
        return InputObservation(**data)

    async def input_text(self):
        if self.input_used:
            raise SocialError("worker_input_already_used")
        self.input_used = True
        if not self._accepted(await self._rpc("input_once")):
            raise SocialError("worker_input_unknown")

    async def submit_once(self):
        if not self.input_used or self.submit_used:
            raise SocialError("worker_submit_already_used")
        self.submit_used = True
        result = await self._rpc("submit_once")
        if set(result) == {"gesture_result"} and self.gesture_result_recorder is not None:
            from .directed_click import ClickResult

            gesture = ClickResult(**result["gesture_result"])
            self.gesture_result_recorder(gesture)
            if gesture.state != "REQUESTED":
                raise SocialError("gesture_result_unknown_or_not_started")
        elif not self._accepted(result):
            raise SocialError("worker_submit_unknown")

    @staticmethod
    def _accepted(result):
        return set(result) == {"accepted"} and result["accepted"] is True

    async def stop(self):
        if self.process is None:
            return True
        if self.process.stdin:
            self.process.stdin.close()
        job = self.job
        try:
            if job:
                job.terminate()  # Keep a handle to verify every descendant has exited.
            elif self.process.returncode is None:
                self.process.kill()
            async with asyncio.timeout(1.5):
                await self.process.wait()
                while job and job.active():  # noqa: ASYNC110 - Windows Job has no asyncio notification.
                    await asyncio.sleep(0.01)
            return self.process.returncode is not None
        except TimeoutError:
            return False
        finally:
            if job:
                job.close()
                self.job = None
