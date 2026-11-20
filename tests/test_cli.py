import json
import subprocess
import sys
import unittest
from pathlib import Path


class CLITests(unittest.TestCase):
    def test_installed_command_runs_loopback_demo_and_emits_utf8(self):
        executable = Path(sys.executable).parent / "wechat-agent.exe"
        completed = subprocess.run([str(executable), "demo"], capture_output=True,
                                   encoding="utf-8", check=True, timeout=15)
        result = json.loads(completed.stdout)
        self.assertEqual(result["mode"], "loopback-synthetic")
        self.assertEqual(result["framework_result"], "llm.models.LLMResult")
        self.assertEqual(result["http_requests"], 1)
        self.assertEqual(result["answer"], "H1 本机传输验证成功。")
        self.assertEqual(result["request_id"], "req_h1_demo")
