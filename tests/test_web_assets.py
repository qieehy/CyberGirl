"""Production frontend dependency graph and source-checkout discovery after split."""

import json
import re
from pathlib import Path

import pytest

from wechat_agent.web.assets import static_routes, web_root
from wechat_agent.workbench.native import native_source_root


def test_built_frontend_imports_and_pages_resolve_to_declared_assets():
    root = web_root()
    routes = static_routes("workbench")
    assert routes["/"] == routes["/workbench"]
    assert "/history" in routes and "/sandbox" in routes
    for file, _mime in routes.values():
        text = (root / file).read_text(encoding="utf-8")
        if file.endswith(".html"):
            for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', text):
                assert asset in routes, asset
        elif file.endswith(".js"):
            for relative in re.findall(r"(?:from\s*|import\s*)[\"\'](\.[^\"\']+)", text):
                dependency = (root / file).parent.joinpath(relative).resolve()
                assert dependency.is_relative_to(root.resolve()) and dependency.is_file(), relative
                assert any((root / item[0]).resolve() == dependency for item in routes.values())


def test_manifest_cannot_serve_runtime_files(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_AGENT_WEB_DIR", str(tmp_path))
    assert static_routes() == {}
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {"/assets/leak": {"file": "../credentials.json", "type": "text/plain"}},
            }
        )
    )
    with pytest.raises(ValueError, match="path"):
        static_routes()


def test_wechat_workers_still_resolve_from_workspace():
    workspace = Path(__file__).resolve().parents[1]
    assert native_source_root() == workspace
    assert (native_source_root() / "scripts/wechat_private_reader_worker.py").is_file()
