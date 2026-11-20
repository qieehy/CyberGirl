"""A supplied marker can select only the exact peer, time range and bounded candidate set."""

import hashlib
import sqlite3

import pytest
import zstandard

from wechat_agent.bridges.wechat.recent_marker import marker_boundary
from wechat_agent.social.models import SocialError


def database():
    db = sqlite3.connect(":memory:")
    table = "Msg_" + hashlib.md5(b"wxid_peer", usedforsecurity=False).hexdigest()
    db.execute("CREATE TABLE Name2Id(user_name TEXT)")
    db.executemany("INSERT INTO Name2Id VALUES(?)", [("wxid_peer",), ("self",)])
    db.execute(f'''CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY AUTOINCREMENT,local_type INTEGER,
        real_sender_id INTEGER,create_time INTEGER,message_content BLOB,sort_seq INTEGER,WCDB_CT_message_content INTEGER)''')
    return db, table


def test_exact_recent_peer_compressed_marker_selects_preceding_position():
    db, table = database()
    with db:
        for sender, when, text, seq in [(1, 1, "marker", 1), (2, 100, "marker", 2), (1, 100, "other", 3)]:
            db.execute(f'INSERT INTO "{table}" VALUES(NULL,1,?,?,?,?,0)', (sender, when, text, seq))
        blob = zstandard.ZstdCompressor().compress(b"marker")
        db.execute(f'INSERT INTO "{table}" VALUES(NULL,1,1,100,?,4,4)', (blob,))
        found = marker_boundary(db, "wxid_peer", "marker", 90, 110)
        assert found["selected_local_id"] == 4 and found["start"] == dict(sort_seq=3, local_id=3)
        assert found["local_fence"] == 3 and found["candidates"] == 2
    db.close()


@pytest.mark.parametrize("damage", ["duplicate", "too_many", "time_range", "wrong_text", "only_self", "oversize"])
def test_recent_marker_refuses_ambiguity_or_never_matches_outside_scope(damage):
    db, table = database()
    with db:
        count = 33 if damage == "too_many" else 2 if damage == "duplicate" else 1
        for seq in range(1, count + 1):
            db.execute(f'INSERT INTO "{table}" VALUES(NULL,1,?,100,?, ?,0)',
                (2 if damage == "only_self" else 1, "x" * 16001 if damage == "oversize" else "different" if damage == "wrong_text" else "marker", seq))
        if damage in {"duplicate", "too_many", "time_range", "oversize"}:
            with pytest.raises(SocialError):
                marker_boundary(db, "wxid_peer", "marker", 0 if damage == "time_range" else 90, 2000 if damage == "time_range" else 110)
        else:
            assert marker_boundary(db, "wxid_peer", "marker", 90, 110) is None
    db.close()
