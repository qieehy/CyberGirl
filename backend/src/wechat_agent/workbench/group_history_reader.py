"""Selected-group history, no input/model/send/cursor operations."""

import hashlib
from datetime import datetime, timedelta, timezone

from ..bridges.wechat.reader import conversation_table
from ..bridges.wechat.textcodec import decode_text
from ..social.models import SocialError


def read_selected_group(source, open_selected, open_shard, name, limit=200, *, kind="group", expected_id=None):
    if (
        kind not in {"group", "dm"}
        or not isinstance(name, str)
        or not 1 <= len(name.strip()) <= 128
        or type(limit) is not int
        or not 1 <= limit <= 200
    ):
        raise SocialError("invalid_group_history_request")
    contact_path = source.account / "db_storage/contact/contact.db"
    if not contact_path.is_file():
        raise RuntimeError("contact database not found")
    contact = open_selected(contact_path)
    tables = [
        r[0]
        for r in contact.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('contact','Contact','WCContact')"
        )
    ]
    matches = {}
    metadata = None
    for table in tables:
        columns = {r[1] for r in contact.execute('PRAGMA table_info("' + table + '")')}
        usercol = next((n for n in ("username", "user_name", "UserName") if n in columns), None)
        labels = [
            n for n in ("nick_name", "nickname", "remark", "NickName", "Remark") if n in columns
        ]
        if not usercol or not labels:
            continue
        sql = (
            'SELECT "'
            + usercol
            + '",'
            + ",".join('"' + n + '"' for n in labels)
            + ' FROM "'
            + table
            + '" WHERE ('
            + " OR ".join('"' + n + '"=?' for n in labels)
            + ') AND "'
            + usercol
            + ('" LIKE ? LIMIT 6' if kind == "group" else '" NOT LIKE ? LIMIT 6')
        )
        for row in contact.execute(sql, [name] * len(labels) + ["%@chatroom"]):
            matches[row[0]] = row
            metadata = (table, usercol, labels)
    if len(matches) != 1:
        raise SocialError("group_name_not_found" if not matches else "group_name_ambiguous")
    group = next(iter(matches))
    selected_id = hashlib.sha256(group.encode()).hexdigest()
    if expected_id is not None and selected_id != expected_id:
        raise SocialError("group_history_scope_mismatch")
    if kind == "dm" and (group in {"filehelper", source.username} or group.startswith(("gh_", "@"))):
        raise SocialError("private_peer_source_required")
    # Discover message shards against the selected canonical group; do not bind
    # it to the private receiver or modify any persistent cursor.
    source.target_username = group
    source.manifest()
    candidates = []
    for shard, (path, key, salt, _identity) in source.shards.items():
        connection = open_shard(path, key, salt)
        try:
            table = conversation_table(connection, group)
        except Exception as exc:
            if getattr(exc, "code", None) == "conversation_not_in_selected_shard":
                continue
            raise
        columns = {r[1] for r in connection.execute('PRAGMA table_info("' + table + '")')}
        server = next(
            (c for c in ("server_id", "message_svr_id", "msg_svr_id") if c in columns), None
        )
        sql = (
            "SELECT m.local_id,m.sort_seq,m.create_time,m.local_type,n.user_name,"
            + ('m."' + server + '"' if server else "NULL")
            + ' FROM "'
            + table
            + '" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id ORDER BY m.sort_seq DESC,m.local_id DESC LIMIT '
            + str(limit)
        )
        for row in connection.execute(sql):
            candidates.append(
                dict(
                    local_id=row[0],
                    sort_seq=row[1],
                    time=row[2],
                    type=row[3],
                    sender=row[4],
                    server_id=row[5],
                    shard=shard,
                    table=table,
                    connection=connection,
                    columns=columns,
                )
            )
    selected = []
    seen = set()
    for item in sorted(
        candidates,
        key=lambda r: (r["sort_seq"], r["time"], r["shard"], r["local_id"]),
        reverse=True,
    ):
        identity = (
            ("server", item["server_id"])
            if item["server_id"]
            else ("local", item["shard"], item["local_id"])
        )
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(item)
        if len(selected) == limit:
            break
    selected.reverse()
    senders = {r["sender"] for r in selected if r["sender"]}
    labels = {}
    if metadata and senders:
        table, usercol, labelcols = metadata
        rows = contact.execute(
            'SELECT "'
            + usercol
            + '",'
            + ",".join('"' + n + '"' for n in labelcols)
            + ' FROM "'
            + table
            + '" WHERE "'
            + usercol
            + '" IN ('
            + ",".join("?" for _ in senders)
            + ")",
            list(senders),
        )
        for row in rows:
            labels[row[0]] = next((v for v in row[1:] if isinstance(v, str) and v), row[0])
    result = []
    types = {
        3: "图片",
        34: "语音",
        43: "视频",
        47: "表情",
        48: "位置",
        49: "分享/文件/引用",
        10000: "系统消息",
    }
    zone = timezone(timedelta(hours=8))
    for index, item in enumerate(selected, 1):
        compression = (
            '"WCDB_CT_message_content"' if "WCDB_CT_message_content" in item["columns"] else "NULL"
        )
        content, error = None, None
        raw = (
            item["connection"]
            .execute(
                "SELECT CASE WHEN length(CAST(message_content AS BLOB))<=16000 THEN message_content ELSE NULL END,"
                + compression
                + ' FROM "'
                + item["table"]
                + '" WHERE local_id=?',
                (item["local_id"],),
            )
            .fetchone()
        )
        if item["type"] == 1:
            try:
                content = decode_text(raw[0], raw[1])
            except Exception as exc:
                error = getattr(exc, "code", type(exc).__name__)
        else:
            content = "[" + types.get(item["type"], "消息类型 " + str(item["type"])) + "]"
        result.append(
            dict(
                number=index,
                time=datetime.fromtimestamp(item["time"], zone).isoformat(),
                sender=labels.get(item["sender"], item["sender"] or "[未知发送者]"),
                sender_id=item["sender"],
                type=item["type"],
                text=content,
                decode_error=error,
                shard=item["shard"],
                local_id=item["local_id"],
                server_id=item["server_id"],
            )
        )
    source.validate()
    record = dict(
        group=name,
        group_id_sha256=selected_id,
        kind=kind,
        selection="latest messages available locally, displayed oldest to newest",
        count=len(result),
        messages=result,
        model_calls=0,
        send_calls=0,
        ledger_writes=0,
        group_window_opened=False,
        nontext_attachments_downloaded=False,
    )
    return record
