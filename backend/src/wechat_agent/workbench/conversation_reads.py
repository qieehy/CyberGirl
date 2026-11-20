"""Account-scoped, persistent read-only subscriptions. No inbox/model/send wiring."""

import asyncio
import hashlib
import json
import os
import time

from ..bridges.wechat.send_process import unique_object
from ..social.models import SocialError
from .group_history import GroupHistory, validate_request


class ConversationReads:
    def __init__(self, history, *, interval=30):
        self.history = history
        self.interval = interval
        self.path = history.directory / "subscriptions.json"
        self.entries, self.tasks, self.status = {}, {}, {}
        self.lock = asyncio.Lock()
        self.slot = asyncio.Semaphore(1)
        self.running = False
        self.lease = None
        self._load()

    def _load(self):
        self.entries = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            if data.get("account_fingerprint") != self.history.fingerprint:
                raise SocialError("group_history_scope_mismatch")
            for item in data["subscriptions"]:
                self._validate(item["kind"], item["name"])
                if (item["id"] != self.key(item["kind"], item["name"])
                        or type(item["enabled"]) is not bool
                        or len(item["target_id"]) != 64
                        or any(c not in "0123456789abcdef" for c in item["target_id"])):
                    raise SocialError("group_history_scope_mismatch")
                self.entries[item["id"]] = item

    def _acquire(self):
        if self.lease is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.with_suffix(".lock").open("a+b")
        try:
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise SocialError("read_manager_in_use") from None
        self.lease = handle
        try:
            self._load()
        except Exception:
            self._release()
            raise

    def _release(self):
        if self.lease:
            self.lease.close()
            self.lease = None

    @staticmethod
    def key(kind, name):
        return hashlib.sha256((kind + ":" + name).encode()).hexdigest()

    @staticmethod
    def _validate(kind, name):
        if kind not in {"group", "dm"}:
            raise SocialError("invalid_request")
        validate_request(name, 200, False)

    def _cache(self, kind):
        return GroupHistory(self.history.directory / "conversations" / kind,
                            self.history.fingerprint)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(account_fingerprint=self.history.fingerprint,
            subscriptions=list(self.entries.values())), ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    async def _refresh(self, item):
        loader = getattr(self.history, "conversation_loader", None)
        if loader is None:
            raise SocialError("group_history_unavailable")
        async with self.slot:
            data = await loader(item["kind"], item["name"], item.get("target_id"))
            if (data.get("group") != item["name"] or data.get("kind") != item["kind"]
                    or (item.get("target_id") and data.get("group_id_sha256") != item["target_id"])):
                raise SocialError("group_history_scope_mismatch")
            return self._cache(item["kind"]).save(data)

    async def add(self, kind, name):
        self._validate(kind, name)
        async with self.lock:
            self._acquire()
            key = self.key(kind, name)
            if key in self.entries:
                return self.snapshot()
            if len(self.entries) >= 20:
                raise SocialError("subscription_limit")
            item = dict(id=key, kind=kind, name=name, enabled=True)
            # Resolve uniquely before subscribing; future reads pin this canonical identity.
            data = await self._refresh(item)
            item["target_id"] = data["group_id_sha256"]
            self.entries[key] = item
            try:
                self._save()
            except Exception:
                del self.entries[key]
                raise
            self.status[key] = dict(phase="waiting", last_success=time.time(), last_error=None)
            self._launch(item)
            return self.snapshot()

    async def control(self, id, enabled, remove=False):
        if type(enabled) is not bool or type(remove) is not bool or not isinstance(id, str):
            raise SocialError("invalid_request")
        async with self.lock:
            self._acquire()
            if id not in self.entries:
                raise SocialError("not_found")
            old = dict(self.entries[id])
            self.entries[id]["enabled"] = enabled
            if remove:
                del self.entries[id]
            try:
                self._save()
            except Exception:
                self.entries[id] = old
                raise
            task = self.tasks.pop(id, None)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.status.pop(id, None)
            if not remove:
                self._launch(self.entries[id])
            return self.snapshot()

    async def read(self, id):
        if not isinstance(id, str) or id not in self.entries:
            raise SocialError("not_found")
        item = self.entries[id]
        return await self._cache(item["kind"]).read(item["name"])

    def snapshot(self):
        return dict(read_only=True, model_calls=0, send_calls=0, interval=self.interval,
            subscriptions=[dict(item, **self.status.get(key, {}),
                running=key in self.tasks and not self.tasks[key].done())
                for key, item in self.entries.items()])

    def _launch(self, item):
        if self.running and item["enabled"] and item["id"] not in self.tasks:
            self.tasks[item["id"]] = asyncio.create_task(self._loop(item))

    def start(self):
        if self.entries:
            self._acquire()
        self.running = True
        for item in self.entries.values():
            self._launch(item)

    async def _loop(self, item):
        failures = 0
        state = self.status.setdefault(item["id"], {})
        while True:
            state["phase"] = "reading"
            try:
                await self._refresh(item)
                failures = 0
                state.update(phase="waiting", last_success=time.time(), last_error=None)
            except Exception as exc:
                failures += 1
                state.update(phase="retrying", last_error=getattr(exc, "code", "history_read_failed"))
            delay = min(300, self.interval * 2 ** min(failures, 4))
            state["next_read_at"] = time.time() + delay
            await asyncio.sleep(delay)

    async def aclose(self):
        self.running = False
        tasks = list(self.tasks.values())
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._release()


async def subscription_route(manager, method, path, data):
    from ..console import _fields

    if method == "GET" and path == "/api/wechat/reads":
        return manager.snapshot()
    if method == "POST" and path == "/api/wechat/reads/add":
        _fields(data, {"kind", "name"})
        return await manager.add(**data)
    if method == "POST" and path == "/api/wechat/reads/control":
        _fields(data, {"id", "enabled", "remove"})
        return await manager.control(**data)
    if method == "POST" and path == "/api/wechat/reads/messages":
        _fields(data, {"id"})
        return await manager.read(**data)
    raise SocialError("not_found")
