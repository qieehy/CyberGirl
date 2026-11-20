"""Static profile checks run in isolated processes and never access native memory."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

DLL = Path(r"C:\Program Files\Tencent\Weixin\4.2.0.13\Weixin.dll")


def isolated(source):
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_late_profile_selection_is_rejected_before_any_file_or_native_access():
    result = isolated("""
import json
from scripts import probe_wechat_standalone
from scripts import wechat_42013_profile as profile
try:
    profile.activate()
except ValueError as exc:
    print(json.dumps(dict(reason=str(exc), activated=profile._ACTIVATED)))
""")
    assert result == dict(reason="profile must be selected before loading native entry points", activated=False)


@pytest.mark.skipif(not DLL.is_file(), reason="pinned 4.2.0.13 research binary not installed")
def test_profile_qualifies_disk_and_preserves_default_profile_in_parent():
    from scripts.verify_wechat_accessibility import DLL_SHA
    old = DLL_SHA
    result = isolated("""
import json
from scripts import wechat_42013_profile as profile
profile.activate()
from scripts import verify_wechat_accessibility as verify
from wechat_agent.bridges.wechat import qt_memory, qt_connections
assert len(qt_connections.LAYOUT_PROOFS) == 13
profile.activate()
print(json.dumps(dict(sha=verify.DLL_SHA, cache=qt_memory.CACHE_INSTANCE_RVA, signal=qt_connections.SIGNAL_LINEAGE)))
""")
    assert result["sha"] == "afe3d02c58bada3c35009c0cd58ee1f57b2a00fe53237d0dce649107fddb9a20"
    assert result["cache"] == 29111952
    assert result["signal"] == [[152543608, 6], [152540752, 0], [152054984, 4], [151803304, 3]]
    from scripts.verify_wechat_accessibility import DLL_SHA as after
    assert after == old == "a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4"


@pytest.mark.skipif(not DLL.is_file(), reason="pinned 4.2.0.13 research binary not installed")
def test_altered_layout_proof_rejects_profile_before_changing_version_constants():
    result = isolated("""
import json
from scripts import wechat_42013_profile as profile
from scripts.research_wechat_42013_landmarks import Image
from scripts import verify_wechat_accessibility as verify
original = Image.at
def altered(self, rva, size):
    data = original(self, rva, size)
    if rva == 2778027:
        return bytes([data[0] ^ 1]) + data[1:]
    return data
Image.at = altered
try:
    profile.activate()
except ValueError as exc:
    print(json.dumps(dict(reason=str(exc), activated=profile._ACTIVATED, sha=verify.DLL_SHA)))
""")
    assert result["reason"] == "4.2.0.13 Qt layout proof changed"
    assert result["activated"] is False
    assert result["sha"] == "a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4"
