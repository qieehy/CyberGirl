"""Three regression scenarios for the concrete review findings; entirely offline."""

import asyncio
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm.models import AssistantMessage, LLMResult

from wechat_agent.chat import ChatEngine
from wechat_agent.cli import chat_command
from wechat_agent.providers.codex import ErrorCode, ProviderError
from wechat_agent.providers.codex.catalog import CatalogModel, ModelCatalog
from wechat_agent.verification import run_probe, verify_command

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".build" / "review-tests"


class ReviewFixes(unittest.TestCase):
    def test_build_rejections_preserve_locked_wheel_and_matching_build_can_publish(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        spec = importlib.util.spec_from_file_location("review_builder", ROOT / "scripts/build_framework.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("review fixture", encoding="utf-8")
            (source / "pyproject.toml").write_text('''[project]
name = "ai-agent"
version = "0.3.0"
[tool.setuptools.packages.find]
include = ["llm*", "memory*", "tools*", "runtime*", "config*", "errors*", "rag*"]
''', encoding="utf-8")
            wheel = root / ".build/wheels/ai_agent-0.3.0-py3-none-any.whl"
            lock = root / "framework.lock.json"
            payload = b"reviewed-wheel"
            failure = False

            def build(command, **kwargs):
                output = Path(command[command.index("--wheel-dir") + 1])
                self.assertNotEqual(output, wheel.parent)
                (output / wheel.name).write_bytes(payload)
                if failure:
                    raise subprocess.CalledProcessError(1, command)

            with patch.object(builder, "ROOT", root), patch.object(sys, "argv", ["build", str(source)]), \
                    patch.object(builder.subprocess, "run", side_effect=build) as compiler, redirect_stdout(io.StringIO()):
                builder.main()
                original_wheel, original_lock = wheel.read_bytes(), lock.read_bytes()
                (source / "README.md").write_text("changed-source", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    builder.main()
                self.assertEqual(compiler.call_count, 1, "Source drift must stop before building")
                (source / "README.md").write_text("review fixture", encoding="utf-8")
                payload = b"unreviewed-build"
                with self.assertRaises(SystemExit):
                    builder.main()
                self.assertEqual(wheel.read_bytes(), original_wheel)
                self.assertEqual(lock.read_bytes(), original_lock)
                failure = True
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.main()
                self.assertEqual(wheel.read_bytes(), original_wheel)
                self.assertEqual(lock.read_bytes(), original_lock)
                failure, payload = False, original_wheel
                builder.main()
                self.assertEqual(wheel.read_bytes(), original_wheel)
                self.assertEqual(lock.read_bytes(), original_lock)
                self.assertEqual(json.loads(lock.read_text())["wheel_sha256"], hashlib.sha256(original_wheel).hexdigest())

    def test_chat_keeps_confirmed_context_after_input_and_network_errors_without_replay(self):
        seen = []

        async def model(messages, tools, **kwargs):
            seen.append(messages)
            if messages[-1]["content"] == "network-failure":
                raise ProviderError(ErrorCode.CONNECTION)
            if messages[-1]["content"] == "auth-failure":
                raise ProviderError(ErrorCode.AUTH)
            return LLMResult(AssistantMessage("answer:" + messages[-1]["content"]))

        @asynccontextmanager
        async def factory():
            async with ChatEngine(model, lambda: "review-epoch") as engine:
                yield SimpleNamespace(engine=engine)

        output, errors = io.StringIO(), io.StringIO()
        messages = "first\n" + "x" * 8001 + "\nnetwork-failure\nafter\n/exit\n"
        with patch("wechat_agent.bootstrap.open_mock_application", factory), \
                patch("sys.stdin", io.StringIO(messages)), redirect_stdout(output), redirect_stderr(errors):
            chat_command("mock", None, SCRATCH)
        self.assertEqual([m[-1]["content"] for m in seen], ["first", "network-failure", "after"])
        self.assertEqual([m["content"] for m in seen[-1][1:]], ["first", "answer:first", "after"])
        self.assertIn("answer:after", output.getvalue())
        self.assertEqual(errors.getvalue().count("未自动重发"), 2)
        with patch("wechat_agent.bootstrap.open_mock_application", factory), redirect_stdout(io.StringIO()):
            with self.assertRaises(ProviderError) as once:
                chat_command("mock", None, SCRATCH, once="network-failure")
            self.assertEqual(once.exception.code, ErrorCode.CONNECTION)
            with patch("sys.stdin", io.StringIO("auth-failure\nafter\n")):
                with self.assertRaises(ProviderError) as fatal:
                    chat_command("mock", None, SCRATCH)
            self.assertEqual(fatal.exception.code, ErrorCode.AUTH)


class VerificationReviewFix(unittest.IsolatedAsyncioTestCase):
    async def test_failed_and_cancelled_probes_keep_phase_and_attempt_counts_without_text(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        mode = "catalog"

        class Provider:
            http_attempts = 0

            async def __call__(self, *args, **kwargs):
                self.http_attempts = 2  # Synthetic evidence of a 401 replay.
                if mode == "cancel":
                    raise asyncio.CancelledError
                raise ProviderError(ErrorCode.AUTH, status=401)

        class Tokens:
            def status(self):
                return {"state": "ready", "expires_at": 100}

            def context_key(self):
                return "review-epoch"

            async def get_session(self):
                return None

        @asynccontextmanager
        async def factory(*args):
            yield SimpleNamespace(tokens=Tokens(), provider=Provider())

        async def catalog(session):
            if mode == "catalog":
                raise ProviderError(ErrorCode.CONNECTION)
            return ModelCatalog((CatalogModel("review-model", 1),), "review-time", None, "review-epoch")

        async def probe(stage, model, directory, *, progress):
            return await run_probe(stage, model, directory, app_factory=factory, progress=progress)

        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary, \
                patch("wechat_agent.verification.fetch_catalog", side_effect=catalog), \
                patch("wechat_agent.verification.run_probe", side_effect=probe):
            for mode, expected_phase, expected_attempts in [("catalog", "catalog", 0),
                                                          ("model", "model", 2), ("cancel", "model", 2)]:
                output = Path(temporary) / (mode + ".json")
                if mode == "cancel":
                    with self.assertRaises(asyncio.CancelledError):
                        await verify_command("provider", "review-model", SCRATCH, output)
                else:
                    await verify_command("provider", "review-model", SCRATCH, output)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["phase"], expected_phase)
                self.assertEqual(result["model_http_attempts"], expected_attempts)
                self.assertEqual(result["logical_model_calls"], int(expected_phase == "model"))
                self.assertEqual(result["status"], "cancelled" if mode == "cancel" else "failed")
                self.assertNotIn("连接成功", output.read_text(encoding="utf-8"))
                self.assertNotIn("review-epoch", output.read_text(encoding="utf-8"))
