"""Focused deep traversal and binding tests; no desktop or process memory access."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import verify_wechat_accessibility as verifier


def test_real_traversal_on_synthetic_tree_and_role_classification():
    engine = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    run = subprocess.run([str(engine), '-NoProfile', '-NonInteractive', '-File',
                          str(Path(__file__).with_name('uia_tree_fixture.ps1'))],
                         capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr.decode(errors='replace')
    nodes = [dict(index=0, parent=None, class_name='Qt51514QWindowIcon', type='ControlType.Window', role='other'),
             dict(index=1, parent=0, class_name='mmui::XView', type='ControlType.Custom', role='session_list'),
             dict(index=2, parent=0, class_name='mmui::XView', type='ControlType.Pane', role='chat_input')]
    view = dict(nodes=nodes, bounded=False, errors=[])
    assert verifier.summarize_controls({'raw': view})['status'] == 'found'
    nodes[0]['class_name'] = 'mmui::LoginWindow'
    assert verifier.summarize_controls({'raw': view})['status'] == 'wrong_window'
    nodes[0]['class_name'] = 'mmui::MainWindow'
    view['bounded'] = True
    assert verifier.summarize_controls({'raw': view})['status'] == 'incomplete'


def test_collector_binding_generation_and_timeout_cleanup(monkeypatch, tmp_path):
    facts = dict(pid=12, hwnd=34, created_filetime='56')
    output = tmp_path / 'uia.json'
    output.write_text(json.dumps(dict(status='observed', windows=[dict(pid=12, hwnd=34, created_filetime='57')])))
    commands, terminated = [], []

    class Child:
        pid = 987
        returncode = 0
        timeout = False

        def __init__(self, command, **kwargs):
            commands.append(command)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout=None):
            if self.timeout and timeout:
                raise subprocess.TimeoutExpired('synthetic', timeout)

    monkeypatch.setattr(verifier.subprocess, 'Popen', Child)
    monkeypatch.setattr(verifier.subprocess, 'run', lambda command, **kwargs: terminated.append(command))
    with pytest.raises(ValueError, match='binding changed'):
        verifier.observe_controls(facts, output)
    assert commands[0][-10:] == ['-TargetPid', '12', '-TargetHwnd', '34', '-CreatedFiletime', '56', '-MaxDepth', '32', '-MaxNodes', '800']
    Child.timeout = True
    with pytest.raises(subprocess.TimeoutExpired):
        verifier.observe_controls(facts, output)
    assert terminated == [['taskkill.exe', '/PID', '987', '/T', '/F']]
