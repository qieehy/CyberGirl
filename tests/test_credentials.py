import json
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.auth_support import FakeAuth, FakeClock
from wechat_agent.providers.codex import ErrorCode, ProviderError
from wechat_agent.providers.codex.auth_models import AuthEndpoint, AuthState
from wechat_agent.providers.codex.credentials import CredentialRecord, CredentialStore
from wechat_agent.providers.codex.protection import WindowsProtection

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".build" / "h2-tests"


class CredentialTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.folder.cleanup)
        self.clock = FakeClock()
        self.auth = FakeAuth(self.clock)
        self.store = CredentialStore(Path(self.folder.name), self.auth.binding)
        self.store.acquire()
        self.addCleanup(self.store.close)
        self.record = CredentialRecord(self.auth.binding, "a" * 32, AuthState.ACTIVE, self.auth.tokens())

    def test_real_dpapi_ciphertext_hides_secrets_and_reopens(self):
        self.store.save(self.record)
        ciphertext = self.store.path.read_bytes()
        self.assertNotIn(self.record.tokens.access_token.encode(), ciphertext)
        self.assertNotIn(self.record.tokens.refresh_token.encode(), ciphertext)
        self.assertEqual(self.store.load(), self.record)
        self.store.close()
        other = CredentialStore(Path(self.folder.name), self.auth.binding)
        try:
            other.acquire()
            self.assertEqual(other.load(), self.record)
        finally:
            other.close()

    def test_corrupt_or_wrong_route_vault_is_not_overwritten(self):
        self.store.save(self.record)
        ciphertext = self.store.path.read_bytes()[:-1]
        self.store.path.write_bytes(ciphertext)
        with self.assertRaises(ProviderError) as caught:
            self.store.load()
        self.assertEqual(caught.exception.code, ErrorCode.STORAGE)
        self.assertEqual(self.store.path.read_bytes(), ciphertext)
        self.store.save(self.record)
        self.store.close()
        other = CredentialStore(Path(self.folder.name), AuthEndpoint().binding)
        try:
            other.acquire()
            with self.assertRaises(ProviderError):
                other.load()
        finally:
            other.close()

    def test_atomic_replace_failure_keeps_old_vault_and_removes_temporary(self):
        self.store.save(self.record)
        before = self.store.path.read_bytes()
        updated = replace(self.record, tokens=self.auth.tokens(version="new"))
        with patch("wechat_agent.providers.codex.credentials.os.replace", side_effect=PermissionError()):
            with self.assertRaises(ProviderError) as caught:
                self.store.save(updated)
        self.assertEqual(caught.exception.code, ErrorCode.STORAGE)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.load(), self.record)
        self.assertEqual(list(self.store.directory.glob("*.tmp")), [])

    def test_encryption_failure_never_writes_plaintext(self):
        self.store.save(self.record)
        before = self.store.path.read_bytes()
        with patch.object(self.store._protection, "protect", side_effect=ProviderError(ErrorCode.STORAGE)):
            with self.assertRaises(ProviderError):
                self.store.save(self.record)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob("*.tmp")), [])

    def test_owner_is_exclusive_in_same_process_and_cannot_be_deleted(self):
        other = CredentialStore(Path(self.folder.name), self.auth.binding)
        try:
            with self.assertRaises(ProviderError) as caught:
                other.acquire()
            self.assertEqual(caught.exception.code, ErrorCode.BUSY)
            with self.assertRaises(PermissionError):
                (self.store.directory / "owner.lock").unlink()
        finally:
            other.close()

    def test_owner_is_exclusive_across_processes_and_released_on_crash(self):
        code = """
import sys, time
from pathlib import Path
from wechat_agent.providers.codex.auth_models import AuthEndpoint
from wechat_agent.providers.codex.credentials import CredentialStore
from wechat_agent.providers.codex.errors import ProviderError
store=CredentialStore(Path(sys.argv[1]), AuthEndpoint().binding)
try:
    store.acquire()
except ProviderError as error:
    print(error.code.value, flush=True)
    raise SystemExit(23)
print('owned', flush=True)
time.sleep(30)
"""
        blocked = subprocess.run([sys.executable, "-B", "-c", code, self.folder.name],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(blocked.returncode, 23)
        self.assertEqual(blocked.stdout.strip(), ErrorCode.BUSY.value)
        self.store.close()
        child = subprocess.Popen([sys.executable, "-B", "-c", code, self.folder.name],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "owned")
            with self.assertRaises(ProviderError):
                self.store.acquire()
            child.kill()
            child.wait(timeout=10)
            # Process signaling and native file-handle teardown can become
            # observable on adjacent Windows scheduler turns. Never unlink the
            # lock to force access; require reacquisition within a short bound.
            deadline = time.monotonic() + 2
            while True:
                try:
                    self.store.acquire()
                    break
                except ProviderError as exc:
                    if exc.code != ErrorCode.BUSY or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()

    def test_explicit_reset_can_clear_corruption_and_contains_no_tokens(self):
        self.store.path.write_bytes(b"corrupt synthetic vault")
        self.store.reset()
        record = self.store.load()
        self.assertEqual(record.state, AuthState.LOGGED_OUT)
        self.assertIsNone(record.tokens)

    def test_schema_and_version_boundaries(self):
        valid = json.loads(self.record.to_bytes())
        cases = [{**valid, "version": True}, {**valid, "version": 2}, {**valid, "app": "another-app"},
                 {**valid, "generation": "bad"}, {**valid, "state": "unknown"},
                 {**valid, "state": "logged_out"}, {**valid, "retry_at": 100},
                 {**valid, "last_error": "untrusted arbitrary text"}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ProviderError):
                CredentialRecord.from_bytes(json.dumps(value).encode(), self.auth.binding)
        with self.assertRaises(ProviderError):
            CredentialRecord.from_bytes(b'{"version":1,"version":1}', self.auth.binding)

    def test_store_requires_ownership_and_bounds_ciphertext(self):
        self.store.close()
        for operation in [self.store.load, lambda: self.store.save(self.record)]:
            with self.assertRaises(ProviderError):
                operation()
        self.store.acquire()
        self.store.path.write_bytes(b"x" * (256 * 1024 + 1))
        with self.assertRaises(ProviderError):
            self.store.load()

    def test_dpapi_rejects_corruption_and_is_randomized(self):
        protection = WindowsProtection()
        first = protection.protect(b"h2-synthetic-secret")
        second = protection.protect(b"h2-synthetic-secret")
        self.assertNotEqual(first, second)
        self.assertEqual(protection.unprotect(first), b"h2-synthetic-secret")
        with self.assertRaises(ProviderError):
            protection.unprotect(b"not a DPAPI value")
