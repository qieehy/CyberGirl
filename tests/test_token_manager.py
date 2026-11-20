import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.auth_support import FakeAuth, FakeClock, ignore_challenge
from wechat_agent.providers.codex import ErrorCode, ProviderError, SyntheticSession
from wechat_agent.providers.codex.auth_models import AuthState
from wechat_agent.providers.codex.credentials import CredentialRecord, CredentialStore
from wechat_agent.providers.codex.token_manager import TokenManager, storage_call

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".build" / "h2-tests"


class FaultStore(CredentialStore):
    fail_state = None
    block_state = None

    def __init__(self, *args):
        super().__init__(*args)
        self.write_entered = threading.Event()
        self.write_release = threading.Event()

    def save(self, record):
        if record.state == self.fail_state:
            raise ProviderError(ErrorCode.STORAGE)
        if record.state == self.block_state:
            self.write_entered.set()
            if not self.write_release.wait(5):
                raise ProviderError(ErrorCode.STORAGE)
        super().save(record)


class TokenManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.folder.cleanup)
        self.clock = FakeClock()
        self.auth = FakeAuth(self.clock)
        self.store = FaultStore(Path(self.folder.name), self.auth.binding)
        self.managers = []

    def seed(self, *, expired=False, pending=False):
        self.store.acquire()
        try:
            record = CredentialRecord(self.auth.binding, "a" * 32,
                                      AuthState.REFRESH_PENDING if pending else AuthState.ACTIVE,
                                      None if pending else self.auth.tokens(expired=expired))
            self.store.save(record)
        finally:
            self.store.close()

    async def manager(self, auth=None, store=None):
        auth = auth or self.auth
        store = store or self.store
        manager = TokenManager(store, auth, now=lambda: self.clock.now)
        await manager.__aenter__()
        self.managers.append(manager)
        return manager

    async def asyncTearDown(self):
        self.auth.refresh_release.set()
        self.auth.login_release.set()
        self.store.write_release.set()
        for manager in self.managers:
            await manager.aclose()

    async def test_login_is_durable_and_restart_recovers_without_refresh(self):
        manager = await self.manager()
        self.assertEqual(manager.status()["state"], "logged_out")
        await manager.login(ignore_challenge)
        self.assertEqual(manager.status()["state"], "ready")
        self.assertIsInstance(await manager.get_session(), SyntheticSession)
        self.assertEqual(self.auth.refresh_calls, [])
        await manager.aclose()
        self.assertTrue(self.auth.closed)
        other = await self.manager(FakeAuth(self.clock), CredentialStore(Path(self.folder.name), self.auth.binding))
        self.assertEqual(other.status()["state"], "ready")
        self.assertIsInstance(await other.get_session(), SyntheticSession)

    async def test_ten_waiters_share_one_refresh_and_cancelled_waiter_cannot_drop_rotation(self):
        self.seed(expired=True)
        self.auth.refresh_release.clear()
        manager = await self.manager()
        tasks = [asyncio.create_task(manager.get_session()) for _ in range(10)]
        await self.auth.refresh_entered.wait()
        pending = await storage_call(self.store.load)
        self.assertEqual(pending.state, AuthState.REFRESH_PENDING)
        self.assertIsNone(pending.tokens)
        tasks[0].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tasks[0]
        self.auth.refresh_release.set()
        results = await asyncio.gather(*tasks[1:])
        self.assertTrue(all(isinstance(result, SyntheticSession) for result in results))
        self.assertEqual(len(self.auth.refresh_calls), 1)
        saved = await storage_call(self.store.load)
        self.assertEqual(saved.tokens.refresh_token, "h2-synthetic-refresh-new")

    async def test_only_waiter_cancelled_still_commits_refresh(self):
        self.seed(expired=True)
        self.auth.refresh_release.clear()
        manager = await self.manager()
        waiter = asyncio.create_task(manager.get_session())
        await self.auth.refresh_entered.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.auth.refresh_release.set()
        await asyncio.wait_for(manager._refresh_task, 2)
        self.assertEqual((await storage_call(self.store.load)).tokens.refresh_token, "h2-synthetic-refresh-new")

    async def test_failed_pending_write_sends_no_refresh(self):
        self.seed(expired=True)
        before = self.store.path.read_bytes()
        manager = await self.manager()
        self.store.fail_state = AuthState.REFRESH_PENDING
        with self.assertRaises(ProviderError) as caught:
            await manager.get_session()
        self.assertEqual(caught.exception.code, ErrorCode.STORAGE)
        self.assertEqual(self.auth.refresh_calls, [])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(manager.status()["state"], "storage_error")

    async def test_failed_rotated_token_save_blocks_calls_and_restart_requires_login(self):
        self.seed(expired=True)
        manager = await self.manager()
        self.store.fail_state = AuthState.ACTIVE
        with self.assertRaises(ProviderError) as caught:
            await manager.get_session()
        self.assertEqual(caught.exception.code, ErrorCode.STORAGE)
        self.assertEqual(manager.status()["state"], "storage_error")
        self.assertEqual((await storage_call(self.store.load)).state, AuthState.REFRESH_PENDING)
        with self.assertRaises(ProviderError):
            await manager.get_session()
        await manager.aclose()
        other_auth = FakeAuth(self.clock)
        other = await self.manager(other_auth, CredentialStore(Path(self.folder.name), other_auth.binding))
        self.assertEqual(other.status()["state"], "reauthentication_required")
        with self.assertRaises(ProviderError):
            await other.get_session()
        self.assertEqual(other_auth.refresh_calls, [])

    async def test_terminal_and_uncertain_refresh_failure_never_replays_old_token(self):
        for code in [ErrorCode.AUTH, ErrorCode.TIMEOUT, ErrorCode.PROTOCOL, ErrorCode.CONNECTION]:
            with self.subTest(code=code):
                self.seed(expired=True)
                auth = FakeAuth(self.clock)
                auth.refresh_error = ProviderError(code)
                manager = await self.manager(auth)
                for _ in range(2):
                    with self.assertRaises(ProviderError):
                        await manager.get_session()
                self.assertEqual(len(auth.refresh_calls), 1)
                record = await storage_call(self.store.load)
                self.assertEqual(record.state, AuthState.REAUTH_REQUIRED)
                self.assertIsNone(record.tokens)
                await manager.aclose()

    async def test_429_persists_pause_and_retries_after_synthetic_clock_advance(self):
        self.seed(expired=True)
        self.auth.refresh_error = ProviderError(ErrorCode.RATE_LIMIT, retry_after=35)
        manager = await self.manager()
        for _ in range(2):
            with self.assertRaises(ProviderError) as caught:
                await manager.get_session()
            self.assertEqual(caught.exception.code, ErrorCode.RATE_LIMIT)
        self.assertEqual(len(self.auth.refresh_calls), 1)
        saved = await storage_call(self.store.load)
        self.assertEqual(saved.state, AuthState.PAUSED)
        self.assertEqual(saved.tokens.refresh_token, "h2-synthetic-refresh-old")
        self.clock.now += 36
        self.auth.refresh_error = None
        self.assertIsInstance(await manager.get_session(), SyntheticSession)
        self.assertEqual(len(self.auth.refresh_calls), 2)

    async def test_logout_while_refreshing_cannot_restore_credentials(self):
        self.seed(expired=True)
        self.auth.refresh_release.clear()
        manager = await self.manager()
        waiter = asyncio.create_task(manager.get_session())
        await self.auth.refresh_entered.wait()
        await manager.logout()
        self.assertEqual(manager.status()["state"], "logged_out")
        with self.assertRaises(ProviderError) as caught:
            await asyncio.wait_for(manager.get_session(), 0.1)
        self.assertEqual(caught.exception.code, ErrorCode.AUTH)
        self.auth.refresh_release.set()
        with self.assertRaises(ProviderError):
            await waiter
        record = await storage_call(self.store.load)
        self.assertEqual(record.state, AuthState.LOGGED_OUT)
        self.assertIsNone(record.tokens)

    async def test_logout_after_late_429_cannot_restore_old_tokens(self):
        self.seed(expired=True)
        self.auth.refresh_release.clear()
        self.auth.refresh_error = ProviderError(ErrorCode.RATE_LIMIT, retry_after=30)
        manager = await self.manager()
        waiter = asyncio.create_task(manager.get_session())
        await self.auth.refresh_entered.wait()
        await manager.logout()
        self.auth.refresh_release.set()
        with self.assertRaises(ProviderError):
            await waiter
        self.assertEqual((await storage_call(self.store.load)).state, AuthState.LOGGED_OUT)

    async def test_logout_and_cancellation_stop_pending_login(self):
        self.auth.login_release.clear()
        manager = await self.manager()
        task = asyncio.create_task(manager.login(ignore_challenge))
        await self.auth.login_entered.wait()
        self.assertEqual(manager.status()["state"], "waiting_for_login")
        await manager.logout()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.auth.login_cancelled.is_set())
        self.assertEqual((await storage_call(self.store.load)).state, AuthState.LOGGED_OUT)

    async def test_cancelled_login_caller_persists_logged_out(self):
        self.auth.login_release.clear()
        manager = await self.manager()
        task = asyncio.create_task(manager.login(ignore_challenge))
        await self.auth.login_entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(manager.status()["state"], "logged_out")

    async def test_close_finishes_rotation_and_cancelled_close_waiter_does_not_release_lease(self):
        self.seed(expired=True)
        self.auth.refresh_release.clear()
        manager = await self.manager()
        waiter = asyncio.create_task(manager.get_session())
        await self.auth.refresh_entered.wait()
        closing = asyncio.create_task(manager.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        other = CredentialStore(Path(self.folder.name), self.auth.binding)
        with self.assertRaises(ProviderError) as caught:
            other.acquire()
        self.assertEqual(caught.exception.code, ErrorCode.BUSY)
        self.auth.refresh_release.set()
        await manager.aclose()
        with self.assertRaises(ProviderError):
            await waiter
        other.acquire()
        try:
            self.assertEqual(other.load().tokens.refresh_token, "h2-synthetic-refresh-new")
        finally:
            other.close()

    async def test_cancelled_logout_waits_for_native_write_and_publishes_logged_out(self):
        self.seed()
        manager = await self.manager()
        self.store.block_state = AuthState.LOGGED_OUT
        operation = asyncio.create_task(manager.logout())
        try:
            self.assertTrue(await asyncio.to_thread(self.store.write_entered.wait, 2))
            operation.cancel()
            await asyncio.sleep(0)
            self.assertFalse(operation.done())
        finally:
            self.store.write_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        with self.assertRaises(ProviderError):
            await manager.get_session()
        self.assertEqual(manager.status()["state"], "logged_out")
        self.assertEqual((await storage_call(self.store.load)).state, AuthState.LOGGED_OUT)

    async def test_real_process_crash_marker_requires_login_without_refresh(self):
        code = """
import os,sys
from pathlib import Path
from wechat_agent.providers.codex.auth_models import AuthEndpoint,AuthState
from wechat_agent.providers.codex.credentials import CredentialStore,CredentialRecord
store=CredentialStore(Path(sys.argv[1]),AuthEndpoint(sys.argv[2],local_testing=True).binding)
store.acquire()
store.save(CredentialRecord(store.binding,'a'*32,AuthState.REFRESH_PENDING))
os._exit(37)
"""
        child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", code,
                                                     self.folder.name, self.auth.binding.issuer)
        self.assertEqual(await asyncio.wait_for(child.wait(), 10), 37)
        manager = await self.manager()
        self.assertEqual(manager.status()["state"], "reauthentication_required")
        with self.assertRaises(ProviderError):
            await manager.get_session()
        self.assertEqual(self.auth.refresh_calls, [])

    async def test_status_and_repr_do_not_expose_tokens_account_or_device_code(self):
        self.seed()
        manager = await self.manager()
        status = repr(manager.status())
        record = await storage_call(self.store.load)
        for secret in [record.tokens.access_token, record.tokens.refresh_token, record.tokens.account_id]:
            self.assertNotIn(secret, status)
            self.assertNotIn(secret, repr(record))
            self.assertNotIn(secret, repr(record.tokens))
        self.assertEqual(manager.status()["state"], "ready")
        self.assertEqual(len(manager.status()["account_fingerprint"]), 12)

    async def test_second_manager_is_busy_without_releasing_first_owner(self):
        first = await self.manager()
        other = TokenManager(CredentialStore(Path(self.folder.name), self.auth.binding), FakeAuth(self.clock))
        with self.assertRaises(ProviderError) as caught:
            await other.__aenter__()
        self.assertEqual(caught.exception.code, ErrorCode.BUSY)
        self.assertEqual(first.status()["state"], "logged_out")

    async def test_not_open_and_closed_calls_are_rejected(self):
        manager = TokenManager(self.store, self.auth)
        with self.assertRaises(ProviderError):
            await manager.get_session()
        await manager.aclose()
        with self.assertRaises(ProviderError):
            await manager.get_session()

    async def test_reusing_store_object_cannot_release_another_manager_lease(self):
        first = await self.manager()
        other = TokenManager(self.store, FakeAuth(self.clock))
        with self.assertRaises(ProviderError) as caught:
            await other.__aenter__()
        self.assertEqual(caught.exception.code, ErrorCode.BUSY)
        await other.aclose()
        probe = CredentialStore(Path(self.folder.name), self.auth.binding)
        try:
            with self.assertRaises(ProviderError):
                probe.acquire()
            self.assertEqual(first.status()["state"], "logged_out")
        finally:
            probe.close()
