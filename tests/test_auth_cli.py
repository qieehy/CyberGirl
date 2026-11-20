import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.auth_support import FakeAuth, FakeClock
from wechat_agent.cli import auth_command
from wechat_agent.providers.codex.auth_models import AuthEndpoint, AuthState
from wechat_agent.providers.codex.credentials import CredentialRecord, CredentialStore

SCRATCH = Path(__file__).resolve().parents[1] / ".build" / "h2-tests"


class AuthCLITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name)

    async def test_login_command_wires_manager_and_limits_code_to_login_screen(self):
        auth = FakeAuth(FakeClock(), binding=AuthEndpoint().binding)
        stream = io.StringIO()
        with patch("wechat_agent.cli.CodexAuthClient", return_value=auth), contextlib.redirect_stdout(stream):
            status = await auth_command("login", self.directory)
        self.assertIn("H2-TEST-CODE", stream.getvalue())
        self.assertNotIn("H2-TEST-CODE", repr(status))
        self.assertNotIn("h2-synthetic-refresh", stream.getvalue())
        self.assertEqual(status["state"], "ready")
        self.assertTrue(auth.closed)
        self.assertNotIn(b"h2-synthetic-access", (self.directory / "codex-auth.dpapi").read_bytes())

    async def test_status_never_refreshes_or_requests_device_code(self):
        auth = FakeAuth(FakeClock(), binding=AuthEndpoint().binding)
        store = CredentialStore(self.directory, auth.binding)
        store.acquire()
        try:
            store.save(CredentialRecord(auth.binding, "a" * 32, AuthState.ACTIVE, auth.tokens()))
            before = store.path.read_bytes()
        finally:
            store.close()
        with patch("wechat_agent.cli.CodexAuthClient", return_value=auth):
            result = await auth_command("status", self.directory)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(auth.refresh_calls, [])
        self.assertFalse(auth.login_entered.is_set())
        self.assertEqual(store.path.read_bytes(), before)


class InstalledAuthCLITests(unittest.TestCase):
    def test_installed_status_and_local_logout_use_only_explicit_test_directory(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        executable = Path(sys.executable).parent / "wechat-agent.exe"
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            arguments = [str(executable), "auth", "status", "--state-dir", folder]
            status = subprocess.run(arguments, capture_output=True, encoding="utf-8", check=True, timeout=15)
            self.assertEqual(json.loads(status.stdout)["state"], "logged_out")
            vault = Path(folder) / "codex-auth.dpapi"
            self.assertFalse(vault.exists())
            vault.write_bytes(b"corrupted synthetic vault")
            arguments[2] = "logout"
            logout = subprocess.run(arguments, capture_output=True, encoding="utf-8", check=True, timeout=15)
            self.assertEqual(json.loads(logout.stdout), {"state": "logged_out", "local_only": True})
            arguments[2] = "status"
            restored = subprocess.run(arguments, capture_output=True, encoding="utf-8", check=True, timeout=15)
            self.assertEqual(json.loads(restored.stdout)["state"], "logged_out")
