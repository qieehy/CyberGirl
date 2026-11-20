"""Selected-conversation queries over a verified in-memory snapshot, no polling."""

import hashlib
import re

from ...social.models import SocialError, identifier
from .contracts import Cursor, SourceRecord
from .textcodec import decode_text


def conversation_table(connection, username):
    identifier(username)
    table = "Msg_" + hashlib.md5(username.encode(), usedforsecurity=False).hexdigest()
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        raise SocialError("conversation_not_in_selected_shard")
    required = {"local_id", "local_type", "real_sender_id", "create_time", "message_content", "sort_seq"}
    columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
    if not required <= columns:
        raise SocialError("message_schema_unsupported")
    if "user_name" not in {r[1] for r in connection.execute('PRAGMA table_info("Name2Id")')}:
        raise SocialError("sender_schema_unsupported")
    return table


def baseline(connection, username):
    table = conversation_table(connection, username)
    row = connection.execute(f'SELECT sort_seq,local_id FROM "{table}" ORDER BY sort_seq DESC,local_id DESC LIMIT 1').fetchone()
    return Cursor(*row) if row else Cursor(0, 0)


def forward_guard(connection, username, start, local_fence):
    """Formal receiver requires monotonic local IDs and ordered integer positions."""
    table = conversation_table(connection, username)
    schema = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
    local = next(r for r in connection.execute(f'PRAGMA table_info("{table}")') if r[1] == "local_id")
    if local[2].upper() != "INTEGER" or local[5] != 1 or not re.search(r"\bAUTOINCREMENT\b", schema, re.I):
        raise SocialError("reader_monotonic_ids_unverified")
    if connection.execute(f'''SELECT 1 FROM "{table}" WHERE typeof(sort_seq)!='integer'
            OR typeof(local_id)!='integer' OR sort_seq<0 OR local_id<1 LIMIT 1''').fetchone():
        raise SocialError("invalid_source_position")
    maximum = connection.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"').fetchone()[0]
    if maximum < local_fence:
        raise SocialError("reader_source_truncated")
    if connection.execute(f'''SELECT 1 FROM "{table}" WHERE local_id>?
            AND (sort_seq<? OR (sort_seq=? AND local_id<=?)) LIMIT 1''',
            (local_fence, start.sort_seq, start.sort_seq, start.local_id)).fetchone():
        raise SocialError("reader_sort_sequence_regressed")
    return maximum


def read_new(connection, binding, shard, start: Cursor, received_at, *, limit=100, media=False):
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(start, Cursor):
        raise SocialError("invalid_read_bound")
    table = conversation_table(connection, binding.username)
    columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
    compression = 'm."WCDB_CT_message_content"' if "WCDB_CT_message_content" in columns else "NULL"
    # LEFT JOIN preserves unknown senders as errors; an inner join would silently lose records.
    rows = connection.execute(f'''SELECT m.local_id,m.local_type,m.real_sender_id,m.create_time,
        CASE WHEN m.local_type=1 AND length(CAST(m.message_content AS BLOB))<=16000
             THEN m.message_content ELSE NULL END,m.sort_seq,n.user_name,{compression}
        FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
        WHERE (m.sort_seq>? OR (m.sort_seq=? AND m.local_id>?))
        ORDER BY m.sort_seq,m.local_id LIMIT ?''', (start.sort_seq, start.sort_seq, start.local_id, limit)).fetchall()
    result = []
    for row in rows:
        local_id, message_type, _, occurred_at, content, sequence, sender, compression_type = row
        if not isinstance(sender, str) or not sender:
            raise SocialError("unknown_shard_sender")
        if type(message_type) is not int:
            raise SocialError("message_type_unsupported")
        if message_type == 1:
            content = decode_text(content, compression_type)
            kind = "text"
        elif media and message_type in {3, 34, 47}:
            from .media import MEDIA_TYPES

            kind, content = MEDIA_TYPES[message_type]
        else:
            kind, content = "nontext", ""
        result.append(SourceRecord(binding.account.account_id, binding.account.db_epoch, binding.username,
                                   binding.kind, shard, local_id, sequence, sender, occurred_at, received_at,
                                   content, message_type=kind))
    return tuple(result)
