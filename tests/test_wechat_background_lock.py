import os
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_wechat_bridge import BINDING
from wechat_agent.bridges.wechat.background_lock import (
    MutexApi,
    NamedWriteLock,
    writer_lock_names,
    writer_locks,
)
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.social.models import SocialError


class Api:
    def __init__(self, *, result=0, handle=123, release=True, close=True):
        self.result, self.handle = result, handle
        self.release_ok, self.close_ok = release, close
        self.calls = []

    def create(self, name):
        self.calls.append(("create", name))
        return self.handle

    def wait(self, handle, ms):
        self.calls.append(("wait", handle, ms))
        return self.result

    def release(self, handle):
        self.calls.append(("release", handle))
        return self.release_ok

    def close(self, handle):
        self.calls.append(("close", handle))
        return self.close_ok


@pytest.mark.parametrize("result", [0x80, 0x102, 0xFFFFFFFF, 7])
def test_unavailable_or_abandoned_lock_never_enters(result):
    api = Api(result=result)
    with pytest.raises(SocialError):
        with NamedWriteLock("Local\\test", api=api):
            pytest.fail("unavailable mutex must reject")
    assert ("close", 123) in api.calls
    assert (("release", 123) in api.calls) is (result == 0x80)


def test_create_failure_and_release_failure_are_closed():
    api = Api(handle=None)
    with pytest.raises(SocialError, match="create_failed"):
        with NamedWriteLock("Local\\test", api=api):
            pass
    assert len(api.calls) == 1
    api = Api(release=False)
    with pytest.raises(SocialError, match="cleanup_failed"):
        with NamedWriteLock("Local\\test", api=api):
            pass
    assert api.calls[-1] == ("close", 123)


def test_lock_is_single_use_and_exception_releases_it():
    api = Api()
    lock = NamedWriteLock("Local\\test", api=api)
    with pytest.raises(RuntimeError):
        with lock:
            raise RuntimeError("injected")
    assert api.calls[-2:] == [("release", 123), ("close", 123)]
    with pytest.raises(SocialError, match="already_used"):
        lock.__enter__()


def test_project_lock_then_upstream_lock_with_reverse_cleanup():
    events = []

    class Lock:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            events.append(("open", self.name))

        def __exit__(self, *_):
            events.append(("close", self.name))

    names = writer_lock_names(BINDING)
    with writer_locks(BINDING, factory=Lock):
        pass
    assert events == [("open", names[0]), ("open", names[1]), ("close", names[1]), ("close", names[0])]
    changed_db = replace(BINDING, account=replace(BINDING.account, db_epoch="new-db"))
    assert writer_lock_names(changed_db) == names
    changed_process = replace(BINDING, account=replace(BINDING.account,
                              window=replace(BINDING.account.window, created_filetime=999)))
    assert writer_lock_names(changed_process)[0] != names[0]


def test_second_lock_failure_releases_first():
    events = []

    class Lock:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            if self.name == "Local\\whale_bg_send":
                raise SocialError("injected")
            events.append("first_acquired")

        def __exit__(self, *_):
            events.append("first_released")

    with pytest.raises(SocialError):
        writer_locks(BINDING, factory=Lock)
    assert events == ["first_acquired", "first_released"]


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel mutex")
def test_real_processes_are_excluded_until_owner_releases():
    name = "Local\\WeChatAgent.test." + uuid.uuid4().hex
    script = Path(__file__).with_name("wechat_background_lock_worker.py")
    command = python_worker_command(script, name)
    with NamedWriteLock(name):
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "background_lock_unavailable"
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == "acquired"


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel mutex")
def test_real_abandoned_mutex_is_rejected_and_handle_closed():
    name = "Local\\WeChatAgent.test." + uuid.uuid4().hex
    api = MutexApi()
    # Preserve the kernel object after the owner dies so WAIT_ABANDONED is observable.
    handle = api.create(name)
    child = subprocess.Popen(
        python_worker_command(Path(__file__).with_name("wechat_background_lock_worker.py"), name, "hold"),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Windows anonymous pipes are not selectable. Poll a reader future with a deadline.
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(child.stdout.readline)
            try:
                assert ready.result(timeout=10).strip() == "acquired"
            except BaseException:
                child.kill()
                child.wait(timeout=5)
                raise
        child.kill()
        child.wait(timeout=5)
        with pytest.raises(SocialError, match="abandoned"):
            with NamedWriteLock(name):
                pytest.fail("abandoned owner must be reconciled")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.communicate(timeout=5)
        api.close(handle)
