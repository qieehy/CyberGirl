"""Observe original HWND procedure metadata only. Never call a procedure."""

import hashlib

from ...social.models import SocialError
from .qt_memory import IMAGE_SIZE, Reads


def inspect_window_procedures(memory, static_code, get_procedure, *, kinds=("window", "class")):
    if (type(kinds) is not tuple or not 1 <= len(kinds) <= 2 or len(set(kinds)) != len(kinds)
            or any(kind not in {"window", "class"} for kind in kinds)):
        raise SocialError("invalid_procedure_observation_scope")
    reads = Reads(memory, maximum=1024)
    memory.validate()
    observed = []
    result = {}
    for name in kinds:
        pointer = get_procedure(name)
        if type(pointer) is not int or not memory.base <= pointer < memory.base + IMAGE_SIZE - 128:
            raise SocialError("window_proc_not_a_direct_module_pointer")
        rva = pointer - memory.base
        try:
            expected = static_code(rva, 128)
        except (ValueError, StopIteration):
            raise SocialError("window_proc_outside_executable_section") from None
        code = reads.read(pointer, 128)
        if len(expected) != 128 or code != expected:
            raise SocialError("window_proc_bytes_changed")
        observed.append((name, pointer))
        result[name] = dict(function_rva=rva, code_bytes=128, sha256=hashlib.sha256(code).hexdigest())
    for name, pointer in observed:
        if get_procedure(name) != pointer:
            raise SocialError("window_proc_changed_during_observation")
    reads.stable()
    same = observed[0][1] == observed[1][1] if len(observed) == 2 else None
    return dict(procedures=result, same_procedure=same, stable=True,
                window_procedure_observed="window" in result, class_procedure_observed="class" in result,
                lookup_bytes=reads.used, methods_executed=0, window_messages_sent=0, submission_enabled=False)
