"""Bounded adoption of one explicitly requested, already-sent private peer marker."""

import math

from ...social.models import SocialError
from .contracts import Cursor
from .reader import conversation_table, forward_guard
from .textcodec import decode_text


def marker_boundary(connection, username, marker, earliest, latest):
    if (not isinstance(marker, str) or not 1 <= len(marker) <= 128
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in (earliest, latest))
            or not 0 <= earliest <= latest or latest - earliest > 1830):
        raise SocialError("recent_marker_request_invalid")
    table = conversation_table(connection, username)
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    compression = 'm."WCDB_CT_message_content"' if "WCDB_CT_message_content" in columns else "NULL"
    rows = connection.execute(f'''SELECT m.local_id,m.sort_seq,
        CASE WHEN length(CAST(m.message_content AS BLOB))<=16000 THEN m.message_content ELSE NULL END,{compression}
        FROM "{table}" m JOIN Name2Id n ON n.rowid=m.real_sender_id
        WHERE n.user_name=? AND m.local_type=1 AND m.create_time>=? AND m.create_time<=?
        ORDER BY m.sort_seq DESC,m.local_id DESC LIMIT 33''', (username, earliest, latest)).fetchall()
    if len(rows) > 32:
        raise SocialError("recent_marker_candidate_bound")
    matches = []
    for local_id, sequence, text, compressed in rows:
        if text is None:
            raise SocialError("recent_marker_text_bound")
        if decode_text(text, compressed) == marker:
            matches.append((local_id, sequence))
    if not matches:
        return None
    if len(matches) != 1:
        raise SocialError("recent_marker_not_unique")
    local_id, sequence = matches[0]
    previous = connection.execute(f'''SELECT sort_seq,local_id FROM "{table}"
        WHERE sort_seq<? OR (sort_seq=? AND local_id<?) ORDER BY sort_seq DESC,local_id DESC LIMIT 1''',
        (sequence, sequence, local_id)).fetchone()
    start = Cursor(*previous) if previous else Cursor(0, 0)
    fence = connection.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}" WHERE local_id<?', (local_id,)).fetchone()[0]
    if start.local_id > fence or fence >= local_id:
        raise SocialError("recent_marker_position_unverified")
    forward_guard(connection, username, start, fence)
    return dict(start=dict(sort_seq=start.sort_seq, local_id=start.local_id), local_fence=fence,
                selected_local_id=local_id, selected_sort_seq=sequence, candidates=len(rows))
