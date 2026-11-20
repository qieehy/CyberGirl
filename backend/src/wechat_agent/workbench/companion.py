"""Managed companion/memory domain port for the existing selected-private bridge.

Upstream person cards, tiers, relationship weights and mood math are reused;
storage, model scheduling and delivery are adapters to our existing workbench.
"""

import asyncio
import json
import math
import re
import secrets
from datetime import UTC, datetime

from ..bridges.wechat.background_text import require_background_bmp_text
from ..companion_vendor import mood
from ..companion_vendor.person_card import build_person_card, render_person_card
from ..companion_vendor.person_weight import band_label, band_of, decay_factor
from ..social.models import SocialError

DEFAULTS = dict(
    memory_enabled=True,
    capture_enabled=True,
    recall_enabled=True,
    companion_enabled=True,
    summary_enabled=True,
    summary_batch=20,
    recall_limit=6,
    recall_chars=1800,
    proactive_enabled=False,
    idle_minutes=120,
    interval_minutes=180,
    proactive_daily_limit=2,
    unanswered_limit=1,
    quiet_start="23:00",
    quiet_end="08:00",
)
PROFILE = dict(name="", address="", relationship="", weight=25, notes="")
ROLE = dict(activity="", location="", energy=70, mood="平静", schedule="")


def _text(value, limit, *, empty=True):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value.strip())
        or "\x00" in value
    ):
        raise SocialError("invalid_companion_data")
    return value.strip()


def validate(config, profile, role):
    if (
        type(config) is not dict
        or set(config) != set(DEFAULTS)
        or type(profile) is not dict
        or set(profile) != set(PROFILE)
        or type(role) is not dict
        or set(role) != set(ROLE)
    ):
        raise SocialError("invalid_companion_data")
    for key, value in DEFAULTS.items():
        if type(value) is bool and type(config[key]) is not bool:
            raise SocialError("invalid_companion_data")
    for key, low, high in (
        ("summary_batch", 10, 100),
        ("recall_limit", 1, 12),
        ("recall_chars", 300, 3000),
        ("idle_minutes", 10, 10080),
        ("interval_minutes", 10, 10080),
        ("proactive_daily_limit", 1, 10),
        ("unanswered_limit", 1, 5),
    ):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise SocialError("invalid_companion_data")
    for key in ("quiet_start", "quiet_end"):
        if not isinstance(config[key], str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", config[key]
        ):
            raise SocialError("invalid_companion_data")
    for key, limit in (("name", 80), ("address", 60), ("relationship", 80), ("notes", 600)):
        _text(profile[key], limit)
    if type(profile["weight"]) is not int or not 0 <= profile["weight"] <= 100:
        raise SocialError("invalid_companion_data")
    for key in ("activity", "location"):
        _text(role[key], 120)
    _text(role["schedule"], 1000)
    _text(role["mood"], 120, empty=False)
    if type(role["energy"]) is not int or not 0 <= role["energy"] <= 100:
        raise SocialError("invalid_companion_data")
    mood.validate(role["mood"])


def daily_calls(db, now):
    midnight = now - (now + 8 * 3600) % 86400
    count = db.execute(
        "SELECT COUNT(*) FROM workbench_drafts WHERE started_at>=? AND model_calls=1", (midnight,)
    ).fetchone()[0]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='companion_jobs'").fetchone():
        count += db.execute(
            "SELECT COUNT(*) FROM companion_jobs WHERE created_at>=?", (midnight,)
        ).fetchone()[0]
    return count


class Companion:
    def __init__(self, workbench):
        self.wb, self.ledger = workbench, workbench.ledger
        self.scope = workbench.binding.scope.key()
        self.task = None
        self.last_error = None
        self.reason = "waiting"
        self.started_at = self.next_after = self.ledger.now()
        with self.ledger._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS companion_settings(
                scope TEXT PRIMARY KEY,revision INTEGER NOT NULL,config TEXT NOT NULL,
                profile TEXT NOT NULL,role TEXT NOT NULL,updated_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS companion_memories(
                id TEXT PRIMARY KEY,scope TEXT NOT NULL,version INTEGER NOT NULL,
                content TEXT NOT NULL,kind TEXT NOT NULL,importance INTEGER NOT NULL,
                pinned INTEGER NOT NULL,source TEXT NOT NULL,occurred_at REAL NOT NULL,
                updated_at REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS companion_memory_scope ON companion_memories(scope,deleted)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS companion_timeline(
                id TEXT PRIMARY KEY,scope TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,
                at REAL NOT NULL,summarized INTEGER NOT NULL DEFAULT 0)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS companion_timeline_scope ON companion_timeline(scope,at)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS companion_jobs(
                id TEXT PRIMARY KEY,scope TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,
                created_at REAL NOT NULL,text TEXT NOT NULL DEFAULT '',reason TEXT,
                action_id TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS companion_job_sources(
                job TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(job,source))""")
            db.execute(
                """UPDATE companion_timeline SET summarized=-1 WHERE scope=? AND id IN (
                SELECT s.source FROM companion_job_sources s JOIN companion_jobs j ON j.id=s.job
                WHERE j.scope=? AND j.kind='summary' AND j.state IN ('GENERATING','UNKNOWN'))""",
                (self.scope, self.scope),
            )
            db.execute(
                "UPDATE companion_jobs SET state='UNKNOWN',reason='interrupted' WHERE scope=? AND state IN ('GENERATING','SENDING')",
                (self.scope,),
            )
            # Never import pre-install messages. The floor persists even while capture is off.
            self.floor = db.execute("SELECT COALESCE(MAX(rowid),0) FROM bridge_records").fetchone()[
                0
            ]
            db.execute(
                "INSERT OR IGNORE INTO companion_settings VALUES(?,0,?,?,?,?)",
                (
                    self.scope,
                    json.dumps(DEFAULTS),
                    json.dumps(PROFILE),
                    json.dumps(ROLE, ensure_ascii=False),
                    self.started_at,
                ),
            )
            if db.execute(
                "SELECT 1 FROM companion_jobs WHERE scope=? AND kind='proactive' AND state='UNKNOWN'",
                (self.scope,),
            ).fetchone():
                saved = self.settings()
                if saved["config"]["proactive_enabled"]:
                    saved["config"]["proactive_enabled"] = False
                    db.execute(
                        "UPDATE companion_settings SET config=?,revision=revision+1 WHERE scope=?",
                        (json.dumps(saved["config"]), self.scope),
                    )

    def settings(self):
        r = self.ledger._db.execute(
            "SELECT * FROM companion_settings WHERE scope=?", (self.scope,)
        ).fetchone()
        return dict(
            revision=r["revision"],
            config=json.loads(r["config"]),
            profile=json.loads(r["profile"]),
            role=json.loads(r["role"]),
            updated_at=r["updated_at"],
        )

    def stop(self):
        s = self.settings()
        for key in (
            "memory_enabled",
            "capture_enabled",
            "recall_enabled",
            "companion_enabled",
            "summary_enabled",
            "proactive_enabled",
        ):
            s["config"][key] = False
        result = self.save(s["revision"], s["config"], s["profile"], s["role"])
        sending = self.ledger._db.execute(
            "SELECT 1 FROM companion_jobs WHERE scope=? AND state='SENDING'", (self.scope,)
        ).fetchone()
        if not sending and self.task and not self.task.done():
            self.task.cancel()
        return result

    def _touch(self, db):
        db.execute(
            "UPDATE companion_settings SET revision=revision+1,updated_at=? WHERE scope=?",
            (self.ledger.now(), self.scope),
        )
        db.execute(
            "INSERT INTO workbench_context VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1",
            (self.scope,),
        )
        db.execute(
            "UPDATE workbench_drafts SET state='CANCELLED',reason='companion_changed' WHERE scope=? AND state='READY'",
            (self.scope,),
        )

    def save(self, expected_revision, config, profile, role):
        validate(config, profile, role)
        if type(expected_revision) is not int:
            raise SocialError("invalid_request")
        with self.ledger._transaction() as db:
            if self.settings()["revision"] != expected_revision:
                raise SocialError("version_conflict")
            db.execute(
                "UPDATE companion_settings SET config=?,profile=?,role=? WHERE scope=?",
                (
                    json.dumps(config),
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps(role, ensure_ascii=False),
                    self.scope,
                ),
            )
            self._touch(db)
        self.next_after = self.ledger.now()
        return self.snapshot()

    def memories(self, *, deleted=False):
        return [
            dict(r)
            for r in self.ledger._db.execute(
                "SELECT * FROM companion_memories WHERE scope=? AND deleted=? ORDER BY pinned DESC,updated_at DESC LIMIT 500",
                (self.scope, int(deleted)),
            )
        ]

    def put_memory(
        self, *, id=None, expected_version=None, content, kind="fact", importance=60, pinned=False
    ):
        content = _text(content, 1000, empty=False)
        if (
            not isinstance(kind, str)
            or kind not in {"fact", "preference", "event", "promise", "summary"}
            or type(importance) is not int
            or not 0 <= importance <= 100
            or type(pinned) is not bool
        ):
            raise SocialError("invalid_companion_data")
        with self.ledger._transaction() as db:
            if id is None:
                if len(self.memories()) >= 500:
                    raise SocialError("companion_memory_limit")
                id = secrets.token_hex(16)
                db.execute(
                    "INSERT INTO companion_memories VALUES(?,?,1,?,?,?,?,?,?,?,0)",
                    (
                        id,
                        self.scope,
                        content,
                        kind,
                        importance,
                        int(pinned),
                        "operator",
                        self.ledger.now(),
                        self.ledger.now(),
                    ),
                )
            else:
                row = db.execute(
                    "SELECT * FROM companion_memories WHERE id=? AND scope=? AND deleted=0",
                    (id, self.scope),
                ).fetchone()
                if not row:
                    raise SocialError("not_found")
                if type(expected_version) is not int or row["version"] != expected_version:
                    raise SocialError("version_conflict")
                db.execute(
                    "UPDATE companion_memories SET content=?,kind=?,importance=?,pinned=?,version=version+1,updated_at=? WHERE id=?",
                    (content, kind, importance, int(pinned), self.ledger.now(), id),
                )
                self._exclude_source(db, row["source"])
            self._touch(db)
        return self.snapshot()

    def forget(self, id, expected_version, restore=False):
        if type(restore) is not bool or type(expected_version) is not int:
            raise SocialError("invalid_request")
        with self.ledger._transaction() as db:
            row = db.execute(
                "SELECT * FROM companion_memories WHERE id=? AND scope=?", (id, self.scope)
            ).fetchone()
            if not row:
                raise SocialError("not_found")
            if row["version"] != expected_version:
                raise SocialError("version_conflict")
            if restore and row["deleted"] and len(self.memories()) >= 500:
                raise SocialError("companion_memory_limit")
            db.execute(
                "UPDATE companion_memories SET deleted=?,version=version+1,updated_at=? WHERE id=?",
                (int(not restore), self.ledger.now(), id),
            )
            # Forgetting a source also excludes it from later summary windows.
            if not restore:
                self._exclude_source(db, row["source"])
            self._touch(db)
        return self.snapshot()

    def _exclude_source(self, db, source):
        ids = [source]
        if source.startswith("["):
            try:
                decoded = json.loads(source)
                if type(decoded) is list and all(isinstance(v, str) for v in decoded):
                    ids = decoded
            except ValueError:
                pass
        db.executemany(
            "UPDATE companion_timeline SET summarized=1 WHERE scope=? AND id=?",
            [(self.scope, id) for id in ids],
        )

    def clear(self, expected_revision):
        if type(expected_revision) is not int or expected_revision != self.settings()["revision"]:
            raise SocialError("version_conflict")
        with self.ledger._transaction() as db:
            db.execute(
                "UPDATE companion_memories SET deleted=1,version=version+1 WHERE scope=? AND deleted=0",
                (self.scope,),
            )
            db.execute("UPDATE companion_timeline SET summarized=1 WHERE scope=?", (self.scope,))
            self._touch(db)
        return self.snapshot()

    def capture(self):
        rows = self.ledger._db.execute(
            """SELECT b.rowid AS position,p.*,b.disposition,i.source_verified,i.is_self
            FROM bridge_records b JOIN bridge_message_payloads p USING(message_id)
            LEFT JOIN inbox i ON i.event_id=b.event_id WHERE b.rowid>? AND p.scope=? ORDER BY b.rowid LIMIT 200""",
            (self.floor, self.scope),
        ).fetchall()
        c = self.settings()["config"]
        for row in rows:
            self.floor = row["position"]
            if (
                not c["memory_enabled"]
                or not c["capture_enabled"]
                or row["disposition"] != "OBSERVED"
                or not row["source_verified"]
                or row["is_self"]
                or row["sender_id"] != self.wb.binding.username
                or row["message_type"] != "text"
                or not row["text"]
                or row["received_at"] < self.started_at
            ):
                continue
            self.record(row["message_id"], "user", row["text"], row["occurred_at"])

    def record(self, id, role, text, at):
        c = self.settings()["config"]
        if not c["memory_enabled"] or not c["capture_enabled"]:
            return
        with self.ledger._transaction() as db:
            fresh = db.execute(
                "INSERT OR IGNORE INTO companion_timeline VALUES(?,?,?,?,?,0)",
                (id, self.scope, role, text[:4000], at),
            ).rowcount
            if not fresh:
                return
            # Explicit user requests become source-backed memories immediately, without another model.
            if role == "user" and re.match(r"^(请)?(记住|记一下|别忘了)[：:,，\s]*", text):
                content = re.sub(r"^(请)?(记住|记一下|别忘了)[：:,，\s]*", "", text).strip()[:1000]
                if content and len(self.memories()) < 500:
                    db.execute(
                        "INSERT OR IGNORE INTO companion_memories VALUES(?,?,1,?,'fact',80,1,?,?,?,0)",
                        (id, self.scope, content, id, at, self.ledger.now()),
                    )
            db.execute(
                "DELETE FROM companion_timeline WHERE scope=? AND id IN (SELECT id FROM companion_timeline WHERE scope=? ORDER BY at DESC LIMIT -1 OFFSET 2000)",
                (self.scope, self.scope),
            )

    def recall(self, query):
        s = self.settings()
        if not s["config"]["memory_enabled"] or not s["config"]["recall_enabled"]:
            return []
        terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2}", query.casefold()))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
        terms.update(chinese[i : i + 2] for i in range(len(chinese) - 1))
        factor = decay_factor(s["profile"]["weight"])
        ranked = []
        for row in self.memories():
            hits = sum(term in row["content"].casefold() for term in terms)
            if not hits and not row["pinned"]:
                continue
            age = max(0, (self.ledger.now() - row["occurred_at"]) / 86400)
            score = (
                hits * 8
                + row["pinned"] * 12
                + row["importance"] / 20
                - (0 if row["pinned"] else math.log1p(age) / factor)
            )
            ranked.append((score, row))
        ranked.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
        return [r for _, r in ranked[: s["config"]["recall_limit"]]]

    def context(self, query):
        s = self.settings()
        result = {}
        if s["config"]["memory_enabled"] and s["config"]["recall_enabled"]:
            memories = self.recall(query)
            card = build_person_card(
                user_id=self.wb.binding.username,
                user_name=s["profile"]["name"],
                preferred_address=s["profile"]["address"],
                relationship_label=s["profile"]["relationship"],
                relationship_score=s["profile"]["weight"],
                memories=[
                    dict(
                        r,
                        scope="private",
                        occurred_at=datetime.fromtimestamp(r["occurred_at"], UTC).isoformat(),
                        tier="imprint"
                        if r["pinned"]
                        else "important"
                        if r["importance"] >= 70
                        else "normal",
                    )
                    for r in memories
                ],
            )
            result["person_card"] = render_person_card(card, max_chars=500, max_items=3)
            result["profile_notes"] = s["profile"]["notes"]
            used, selected = 0, []
            for r in memories:
                item = dict(
                    id=r["id"],
                    text=r["content"],
                    at=r["occurred_at"],
                    source=r["source"],
                    use="阶段摘要，请核实" if r["kind"] == "summary" else "相关时可提及",
                )
                size = len(json.dumps(item, ensure_ascii=False))
                if used + size > s["config"]["recall_chars"]:
                    continue
                selected.append(item)
                used += size
            result["memories"] = selected
        if s["config"]["companion_enabled"]:
            result["role_state"] = s["role"]
            result["role_state_note"] = (
                "角色模拟状态仅影响语气与节奏；日程是计划，不证明已实际完成。"
            )
        return result

    def snapshot(self):
        s = self.settings()
        now = self.ledger.now()
        return dict(
            **s,
            scope=self.scope,
            target=self.wb.binding.username,
            upstream=dict(companion="7.2.4 · 领域模块移植", memory="2.6.6 · 领域模块移植"),
            memories=self.memories(),
            trash=self.memories(deleted=True),
            relationship_band=band_label(band_of(s["profile"]["weight"])),
            jobs=[
                dict(r)
                for r in self.ledger._db.execute(
                    "SELECT * FROM companion_jobs WHERE scope=? ORDER BY created_at DESC LIMIT 30",
                    (self.scope,),
                )
            ],
            timeline_count=self.ledger._db.execute(
                "SELECT COUNT(*) FROM companion_timeline WHERE scope=?", (self.scope,)
            ).fetchone()[0],
            model_calls_today=daily_calls(self.ledger._db, now),
            runtime=dict(
                busy=bool(self.task and not self.task.done()),
                reason=self.reason,
                last_error=self.last_error,
                available=callable(getattr(self.wb.generator, "companion_turn", None)),
                auto_reply_enabled=self.wb.auto_reply.enabled,
                next_after=self.next_after,
            ),
        )

    def quiet(self, at):
        c = self.settings()["config"]

        def minutes(value):
            h, m = map(int, value.split(":"))
            return h * 60 + m

        start, end = minutes(c["quiet_start"]), minutes(c["quiet_end"])
        local = datetime.fromtimestamp(at + 8 * 3600, UTC)
        current = local.hour * 60 + local.minute
        return (
            start <= current < end
            if start < end
            else current >= start or current < end
            if start != end
            else False
        )

    def eligible(self, exclude_job=None):
        s, wb, now = self.settings(), self.wb, self.ledger.now()
        c = s["config"]
        if not c["companion_enabled"] or not c["proactive_enabled"]:
            return "proactive_disabled"
        if (
            not wb.auto_reply.enabled
            or not wb.reading
            or wb.closed
            or wb.read_error
            or wb.model_switching
        ):
            return "auto_reply_paused"
        if self.quiet(now):
            return "quiet_hours"
        latest = self.ledger._db.execute(
            "SELECT MAX(at) FROM companion_timeline WHERE scope=? AND role='user'", (self.scope,)
        ).fetchone()[0]
        observed = self.ledger._db.execute(
            """SELECT MAX(p.received_at) FROM bridge_message_payloads p
            JOIN bridge_records b USING(message_id) JOIN inbox i ON i.event_id=b.event_id
            WHERE p.scope=? AND p.sender_id=? AND b.disposition='OBSERVED'
            AND i.source_verified=1 AND i.is_self=0 AND p.received_at>=?""",
            (self.scope, self.wb.binding.username, self.started_at),
        ).fetchone()[0]
        latest = max(latest or 0, observed or 0) or None
        if latest is None:
            return "waiting_new_conversation"
        anchor = max(latest, self.started_at, self.next_after)
        if now - anchor < c["idle_minutes"] * 60:
            return "idle_wait"
        jobs = [
            dict(r)
            for r in self.ledger._db.execute(
                "SELECT * FROM companion_jobs WHERE scope=? AND kind='proactive' AND id!=? ORDER BY created_at DESC",
                (self.scope, exclude_job or ""),
            )
        ]
        if jobs and now - jobs[0]["created_at"] < c["interval_minutes"] * 60:
            return "interval_wait"
        midnight = now - (now + 8 * 3600) % 86400
        if sum(r["created_at"] >= midnight for r in jobs) >= c["proactive_daily_limit"]:
            return "proactive_daily_limit"
        if (
            sum(
                r["created_at"] > latest and r["state"] in {"SENT", "UNKNOWN", "SENDING"}
                for r in jobs
            )
            >= c["unanswered_limit"]
        ):
            return "unanswered_pause"
        return None

    def tick(self):
        self.capture()
        wb = self.wb
        if (self.task and not self.task.done()) or not callable(
            getattr(wb.generator, "companion_turn", None)
        ):
            return
        if (
            not wb.auto_reply.enabled
            or not wb.reading
            or wb.closed
            or wb.model_switching
            or (wb.auto_reply.task and not wb.auto_reply.task.done())
            or (wb.generation_task and not wb.generation_task.done())
        ):
            self.reason = "auto_reply_paused_or_busy"
            return
        s = self.settings()
        if (
            daily_calls(self.ledger._db, self.ledger.now())
            >= wb.preferences.snapshot()["config"]["daily_budget"]
        ):
            self.reason = "model_daily_budget"
            return
        pending = [
            dict(r)
            for r in self.ledger._db.execute(
                "SELECT * FROM companion_timeline WHERE scope=? AND summarized=0 ORDER BY at LIMIT ?",
                (self.scope, s["config"]["summary_batch"]),
            )
        ]
        if (
            s["config"]["memory_enabled"]
            and s["config"]["summary_enabled"]
            and len(pending) >= s["config"]["summary_batch"]
        ):
            self.task = wb._task(self._run("summary", pending))
            return
        self.reason = self.eligible() or "proactive_ready"
        if self.reason == "proactive_ready":
            self.task = wb._task(self._run("proactive", []))

    async def _run(self, kind, timeline):
        wb, s, now = self.wb, self.settings(), self.ledger.now()
        if not wb.auto_reply.enabled or not wb.reading or wb.closed:
            return
        epoch, model, persona = wb.auto_reply.epoch, wb.model, self.ledger.current_persona()
        job = secrets.token_hex(16)
        latest = self.ledger._db.execute(
            "SELECT COALESCE(MAX(b.rowid),0) FROM bridge_records b JOIN bridge_message_payloads p USING(message_id) WHERE p.scope=?",
            (self.scope,),
        ).fetchone()[0]
        bounded, size = [], 0
        for row in timeline:
            item = dict(row, text=row["text"][:1200])
            if size + len(item["text"]) > 11000:
                break
            bounded.append(item)
            size += len(item["text"])
        timeline = bounded
        try:
            if kind == "proactive":
                wb.private_sender.check_ready()
            with self.ledger._transaction() as db:
                if daily_calls(db, now) >= wb.preferences.snapshot()["config"]["daily_budget"]:
                    return
                db.execute(
                    "INSERT INTO companion_jobs(id,scope,kind,state,created_at) VALUES(?,?,?,'GENERATING',?)",
                    (job, self.scope, kind, now),
                )
                db.executemany(
                    "INSERT INTO companion_job_sources VALUES(?,?)",
                    [(job, r["id"]) for r in timeline],
                )
            self.reason = kind + "_generating"
            material = dict(
                context=self.context("最近重要的事情 约定 日程"),
                persona=persona.to_dict(),
                timeline=timeline,
                local_time=datetime.fromtimestamp(now + 8 * 3600, UTC).strftime("%Y-%m-%d %H:%M"),
                reply_max_chars=wb.preferences.snapshot()["config"]["reply_max_chars"],
            )
            if kind == "proactive":
                material["recent_history"] = list(
                    reversed(
                        [
                            dict(role=r["role"], text=r["text"][:500], at=r["at"])
                            for r in self.ledger._db.execute(
                                "SELECT role,text,at FROM companion_timeline WHERE scope=? ORDER BY at DESC LIMIT 12",
                                (self.scope,),
                            )
                        ]
                    )
                )
            async with asyncio.timeout(110):
                result = await wb.generator.companion_turn(kind, material)
            async with wb.operation_lock:
                if (
                    not wb.auto_reply.enabled
                    or wb.auto_reply.epoch != epoch
                    or wb.closed
                    or not wb.reading
                    or wb.model != model
                    or self.ledger.current_persona().version != persona.version
                    or self.settings()["revision"] != s["revision"]
                ):
                    raise SocialError("companion_changed")
                if kind == "summary":
                    self._summary(job, result, timeline)
                else:
                    tail = self.ledger._db.execute(
                        "SELECT COALESCE(MAX(b.rowid),0) FROM bridge_records b JOIN bridge_message_payloads p USING(message_id) WHERE p.scope=?",
                        (self.scope,),
                    ).fetchone()[0]
                    if self.eligible(exclude_job=job) is not None or tail != latest:
                        raise SocialError("companion_context_changed")
                    text = result.get("text") if type(result) is dict else None
                    if text == "":
                        self._finish(job, "SILENT")
                        return
                    require_background_bmp_text(text)
                    if len(text) > material["reply_max_chars"]:
                        raise SocialError("reply_configured_limit")
                    self._finish(job, "SENDING", text=text)

                    def guard():
                        wb._open()
                        tail = self.ledger._db.execute(
                            """SELECT COALESCE(MAX(b.rowid),0)
                            FROM bridge_records b JOIN bridge_message_payloads p USING(message_id)
                            WHERE p.scope=?""",
                            (self.scope,),
                        ).fetchone()[0]
                        if (
                            not wb.auto_reply.enabled
                            or wb.auto_reply.epoch != epoch
                            or wb.model != model
                            or self.settings()["revision"] != s["revision"]
                            or self.ledger.current_persona().version != persona.version
                            or tail != latest
                        ):
                            raise SocialError("companion_context_changed")

                    delivery = await wb.private_sender.send("companion:" + job, text, guard=guard)
                    self._finish(
                        job,
                        "SENT" if delivery.get("local_echo") else "UNKNOWN",
                        text=text,
                        reason=delivery.get("reason"),
                        action=delivery.get("action_id"),
                    )
                    if not delivery.get("local_echo"):
                        self._disable_proactive("companion_send_unconfirmed")
                    else:
                        self.record("companion:" + job, "assistant", text, self.ledger.now())
            self.last_error = None
        except asyncio.CancelledError:
            self._finish(job, "UNKNOWN", reason="interrupted")
            with self.ledger._transaction() as db:
                db.executemany(
                    "UPDATE companion_timeline SET summarized=-1 WHERE id=? AND scope=?",
                    [(r["id"], self.scope) for r in timeline],
                )
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, SocialError) else "companion_task_failed"
            self.last_error = code
            row = self.ledger._db.execute(
                "SELECT state FROM companion_jobs WHERE id=?", (job,)
            ).fetchone()
            self._finish(job, "UNKNOWN" if row and row[0] == "SENDING" else "FAILED", reason=code)
            if kind == "summary":
                # Preserve pending sources but consume this attempted window; no repeated model loop.
                with self.ledger._transaction() as db:
                    db.executemany(
                        "UPDATE companion_timeline SET summarized=-1 WHERE id=? AND scope=?",
                        [(r["id"], self.scope) for r in timeline],
                    )
            elif code not in {"companion_changed", "companion_context_changed"}:
                self._disable_proactive(code)
        finally:
            self.reason = "waiting"

    def summarize_once(self):
        wb = self.wb
        if (
            not wb.auto_reply.enabled
            or not wb.reading
            or wb.closed
            or not self.settings()["config"]["memory_enabled"]
        ):
            raise SocialError("companion_not_running")
        if (
            (self.task and not self.task.done())
            or (wb.generation_task and not wb.generation_task.done())
            or (wb.auto_reply.task and not wb.auto_reply.task.done())
        ):
            raise SocialError("busy")
        if (
            daily_calls(self.ledger._db, self.ledger.now())
            >= wb.preferences.snapshot()["config"]["daily_budget"]
        ):
            raise SocialError("model_daily_budget")
        timeline = [
            dict(r)
            for r in self.ledger._db.execute(
                "SELECT * FROM companion_timeline WHERE scope=? AND summarized<=0 ORDER BY at LIMIT 100",
                (self.scope,),
            )
        ]
        if not timeline:
            raise SocialError("companion_no_sources")
        self.task = wb._task(self._run("summary", timeline))
        return dict(started=True)

    def _finish(self, id, state, *, text="", reason=None, action=None):
        with self.ledger._transaction() as db:
            db.execute(
                "UPDATE companion_jobs SET state=?,text=?,reason=?,action_id=? WHERE id=? AND scope=?",
                (state, text, reason, action, id, self.scope),
            )

    def _disable_proactive(self, reason):
        s = self.settings()
        s["config"]["proactive_enabled"] = False
        self.save(s["revision"], s["config"], s["profile"], s["role"])
        self.last_error = reason

    def _summary(self, job, result, timeline):
        if (
            type(result) is not dict
            or set(result) != {"memories"}
            or type(result["memories"]) is not list
            or len(result["memories"]) > 8
        ):
            raise SocialError("invalid_companion_summary")
        ids = {r["id"] for r in timeline}
        items = []
        for item in result["memories"]:
            if (
                type(item) is not dict
                or set(item) != {"content", "source_ids"}
                or type(item["source_ids"]) is not list
                or not item["source_ids"]
                or any(not isinstance(id, str) or id not in ids for id in item["source_ids"])
            ):
                raise SocialError("invalid_companion_summary")
            items.append((_text(item["content"], 600, empty=False), item["source_ids"]))
        with self.ledger._transaction() as db:
            for text, sources in items:
                if len(self.memories()) >= 500:
                    break
                db.execute(
                    "INSERT INTO companion_memories VALUES(?,?,1,?,'summary',60,0,?,?,?,0)",
                    (
                        secrets.token_hex(16),
                        self.scope,
                        text,
                        json.dumps(sources),
                        self.ledger.now(),
                        self.ledger.now(),
                    ),
                )
            db.executemany(
                "UPDATE companion_timeline SET summarized=1 WHERE id=? AND scope=?",
                [(r["id"], self.scope) for r in timeline],
            )
            self._touch(db)
            db.execute("UPDATE companion_jobs SET state='DONE' WHERE id=?", (job,))
