"""Per-conversation model preference; no chat data or credentials."""

import re

from ..social.models import SocialError


def require_model(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise SocialError("invalid_model")
    return value


def selected_model(ledger, scope, fallback):
    with ledger._transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS workbench_model_selection(
            scope TEXT PRIMARY KEY REFERENCES scopes(scope),model TEXT NOT NULL)""")
        row = db.execute(
            "SELECT model FROM workbench_model_selection WHERE scope=?", (scope,)
        ).fetchone()
        return require_model(row[0] if row else fallback)


def save_model(ledger, scope, model):
    selected_model(ledger, scope, model)
    with ledger._transaction() as db:
        db.execute(
            """INSERT INTO workbench_model_selection VALUES(?,?)
            ON CONFLICT(scope) DO UPDATE SET model=excluded.model""",
            (scope, require_model(model)),
        )
