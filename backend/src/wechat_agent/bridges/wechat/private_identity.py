"""Explicit operator-selected private-chat identity; grants no model/send capability."""

import hashlib

from ...social.models import SocialError
from . import qt_memory as qt
from . import standalone_identity as standalone


def selected_private_identity(memory, runtime_ids, *, root_automation_sha256, account_reader, expected_fingerprint):
    memory.validate()
    account = account_reader()
    if not isinstance(account, str) or hashlib.sha256(account.encode()).hexdigest() != expected_fingerprint:
        raise SocialError("private_identity_account_mismatch")
    reads = qt.Reads(memory)
    interface = qt.accessible_interface(reads, memory.base, qt.runtime_accessible_id(runtime_ids))
    editor, _ = qt.object_from_interface(reads, memory.base, interface)
    if qt.static_meta(reads, memory.base, editor)["class_name"] != "mmui::ChatInputField":
        raise SocialError("private_identity_input_class_mismatch")
    chain, objects = qt.parent_chain(reads, memory.base, editor)
    roots = [(entry, obj) for entry, obj in zip(chain["entries"], objects, strict=True)
             if entry["class_name"] == "mmui::ChatSingleWindow"]
    pages = [(entry, obj) for entry, obj in zip(chain["entries"], objects, strict=True)
             if entry["class_name"] == "mmui::ChatMessagePage"]
    if not chain["complete"] or len(roots) != 1 or len(pages) != 1:
        raise SocialError("private_identity_lineage_not_unique")
    root_info, root = roots[0]
    page_info, page = pages[0]
    if (objects.index(page) >= objects.index(root)
            or root_info["meta_object_rva"] != standalone.SINGLE_META_RVA
            or root_info["qobject_vtable_rva"] != standalone.SINGLE_VTABLE_RVA):
        raise SocialError("private_identity_root_layout_mismatch")
    username = qt._page_session_bytes(reads, memory.base, page, page_info).decode("ascii")
    if username in {account, "filehelper"} or username.endswith("@chatroom"):
        raise SocialError("private_identity_target_kind_denied")
    if hashlib.sha256(("ChatSingleWindow" + username).encode()).hexdigest() != root_automation_sha256:
        raise SocialError("private_identity_root_page_mismatch")
    reads.stable()
    if account_reader() != account:
        raise SocialError("private_identity_account_changed")
    # Only the explicitly invoked local identity probe may persist this private value.
    return dict(username=username, username_sha256=hashlib.sha256(username.encode()).hexdigest(),
                canonical_private_chat_verified=True, lookup_bytes=reads.used, stable=True)
