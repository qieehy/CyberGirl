"""Versioned settings and ordered, declarative rules for one bound conversation."""

import json
import re
from copy import deepcopy

from ..bridges.wechat.background_text import require_background_bmp_text
from ..social.models import ActionKind, Decision, SocialError

DEFAULTS = dict(
    context_pairs=16,
    daily_budget=30,
    reply_max_chars=96,
    images_enabled=True,
    stickers_enabled=True,
    voice_enabled=True,
)


def read_preferences(db, scope):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_preferences'").fetchone():
        row = db.execute("SELECT * FROM workbench_preferences WHERE scope=?", (scope,)).fetchone()
        if row:
            return dict(
                revision=row["revision"],
                config=json.loads(row["config"]),
                rules=json.loads(row["rules"]),
                updated_at=row["updated_at"],
            )
    return dict(revision=0, config=deepcopy(DEFAULTS), rules=[], updated_at=None)


def validate_config(config):
    if type(config) is not dict or set(config) != set(DEFAULTS):
        raise SocialError("invalid_config")
    for key, low, high in (
        ("context_pairs", 0, 16),
        ("daily_budget", 1, 30),
        ("reply_max_chars", 1, 96),
    ):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise SocialError("invalid_config")
    if any(
        type(config[key]) is not bool
        for key in ("images_enabled", "stickers_enabled", "voice_enabled")
    ):
        raise SocialError("invalid_config")
    return deepcopy(config)


def validate_rules(rules):
    if type(rules) is not list or len(rules) > 30:
        raise SocialError("invalid_rules")
    seen = set()
    for rule in rules:
        if type(rule) is not dict or set(rule) != {
            "id",
            "name",
            "enabled",
            "match",
            "pattern",
            "action",
            "reply",
            "case_sensitive",
        }:
            raise SocialError("invalid_rules")
        if (
            not isinstance(rule["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rule["id"])
            or rule["id"] in seen
        ):
            raise SocialError("invalid_rules")
        seen.add(rule["id"])
        if (
            not isinstance(rule["name"], str)
            or not 1 <= len(rule["name"].strip()) <= 60
            or not isinstance(rule["pattern"], str)
            or not 1 <= len(rule["pattern"].strip()) <= 200
            or type(rule["enabled"]) is not bool
            or type(rule["case_sensitive"]) is not bool
            or not isinstance(rule["match"], str)
            or rule["match"] not in {"contains", "exact", "prefix"}
            or not isinstance(rule["action"], str)
            or rule["action"] not in {"reply", "ignore"}
            or not isinstance(rule["reply"], str)
        ):
            raise SocialError("invalid_rules")
        if rule["action"] == "reply":
            require_background_bmp_text(rule["reply"])
        elif rule["reply"]:
            raise SocialError("invalid_rules")
    return deepcopy(rules)


def match_rule(rules, text):
    for rule in rules:
        if not rule["enabled"]:
            continue
        pattern, source = rule["pattern"], text
        if not rule["case_sensitive"]:
            pattern, source = pattern.casefold(), source.casefold()
        matched = (
            (rule["match"] == "contains" and pattern in source)
            or (rule["match"] == "exact" and pattern == source)
            or (rule["match"] == "prefix" and source.startswith(pattern))
        )
        if matched:
            return rule
    return None


def rule_decision(rule):
    return (
        Decision(ActionKind.SILENT, "")
        if rule["action"] == "ignore"
        else Decision(ActionKind.REPLY, rule["reply"])
    )


class Preferences:
    def __init__(self, ledger, binding):
        self.ledger, self.scope = ledger, binding.scope.key()
        with ledger._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workbench_preferences(
                scope TEXT PRIMARY KEY REFERENCES scopes(scope), revision INTEGER NOT NULL,
                config TEXT NOT NULL,rules TEXT NOT NULL,updated_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS workbench_preferences_history(
                scope TEXT NOT NULL,revision INTEGER NOT NULL,config TEXT NOT NULL,
                rules TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scope,revision))""")

    def snapshot(self):
        return read_preferences(self.ledger._db, self.scope)

    def save(self, expected_revision, *, config=None, rules=None):
        if type(expected_revision) is not int:
            raise SocialError("invalid_request")
        with self.ledger._transaction() as db:
            current = read_preferences(db, self.scope)
            if current["revision"] != expected_revision:
                raise SocialError("version_conflict")
            config = validate_config(current["config"] if config is None else config)
            rules = validate_rules(current["rules"] if rules is None else rules)
            if any(
                r["action"] == "reply" and len(r["reply"]) > config["reply_max_chars"]
                for r in rules
            ):
                raise SocialError("rule_reply_too_long")
            revision, now = current["revision"] + 1, self.ledger.now()
            values = (
                self.scope,
                revision,
                json.dumps(config),
                json.dumps(rules, ensure_ascii=False),
                now,
            )
            db.execute(
                """INSERT INTO workbench_preferences VALUES(?,?,?,?,?) ON CONFLICT(scope)
                DO UPDATE SET revision=excluded.revision,config=excluded.config,rules=excluded.rules,updated_at=excluded.updated_at""",
                values,
            )
            db.execute("INSERT INTO workbench_preferences_history VALUES(?,?,?,?,?)", values)
            db.execute(
                """INSERT INTO workbench_context VALUES(?,1) ON CONFLICT(scope)
                DO UPDATE SET revision=revision+1""",
                (self.scope,),
            )
            db.execute(
                "UPDATE workbench_drafts SET state='CANCELLED',reason='settings_changed' WHERE scope=? AND state='READY'",
                (self.scope,),
            )
            db.execute(
                "UPDATE outbox SET state='CANCELLED',reason='settings_changed' WHERE state='QUEUED' AND action_id IN (SELECT action_id FROM workbench_drafts WHERE scope=? AND reason='settings_changed')",
                (self.scope,),
            )
        return self.snapshot()

    def logs(self):
        db = self.ledger._db
        rows = []
        for row in db.execute(
            """SELECT started_at,model,state,reason,model_calls FROM workbench_drafts
            WHERE scope=? ORDER BY started_at DESC LIMIT 60""",
            (self.scope,),
        ):
            rows.append(
                dict(
                    at=row["started_at"],
                    kind="规则" if row["model"] == "local-rule" else "模型",
                    status=row["state"],
                    detail=row["reason"] or row["model"],
                )
            )
        for row in db.execute(
            """SELECT o.state,o.reason,a.started_at FROM outbox o JOIN inbox i USING(event_id)
            LEFT JOIN attempts a USING(action_id) WHERE i.scope=? ORDER BY o.rowid DESC LIMIT 60""",
            (self.scope,),
        ):
            rows.append(
                dict(
                    at=row["started_at"] or 0,
                    kind="发送",
                    status=row["state"],
                    detail=row["reason"] or "",
                )
            )
        midnight = self.ledger.now() - (self.ledger.now() + 8 * 3600) % 86400
        calls = db.execute(
            "SELECT COUNT(*) FROM workbench_drafts WHERE scope=? AND started_at>=? AND model_calls=1",
            (self.scope, midnight),
        ).fetchone()[0]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='companion_jobs'").fetchone():
            calls += db.execute("SELECT COUNT(*) FROM companion_jobs WHERE scope=? AND created_at>=?", (self.scope, midnight)).fetchone()[0]
            for row in db.execute('SELECT kind,state,reason,created_at FROM companion_jobs WHERE scope=? ORDER BY created_at DESC LIMIT 30', (self.scope,)):
                rows.append(dict(at=row['created_at'], kind='记忆整理' if row['kind']=='summary' else '主动联系', status=row['state'], detail=row['reason'] or ''))
        messages = db.execute(
            "SELECT COUNT(*) FROM bridge_message_payloads WHERE scope=?", (self.scope,)
        ).fetchone()[0]
        return dict(
            events=sorted(rows, key=lambda r: r["at"], reverse=True)[:100],
            stats=dict(
                messages=messages,
                model_calls_today=calls,
                sent_with_echo=db.execute(
                    """SELECT COUNT(DISTINCT e.action_id) FROM delivery_evidence e
                                   JOIN outbox o USING(action_id) JOIN inbox i USING(event_id)
                                   WHERE i.scope=? AND e.kind='local_echo' """,
                    (self.scope,),
                ).fetchone()[0],
            ),
        )
