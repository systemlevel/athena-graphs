from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "athena-graphs"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_manifests_and_package_share_identity_and_version():
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    package = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))

    assert codex["name"] == claude["name"] == "athena-graphs"
    assert codex["version"] == claude["version"] == package["project"]["version"]
    assert codex["mcpServers"] == claude["mcpServers"] == "./.mcp.json"


def test_both_marketplaces_point_to_the_self_contained_plugin():
    codex = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude = _json(ROOT / ".claude-plugin" / "marketplace.json")

    assert codex["name"] == claude["name"] == "athena-graphs"
    assert codex["plugins"][0]["source"]["path"] == "./plugins/athena-graphs"
    assert claude["plugins"][0]["source"] == "./plugins/athena-graphs"


def test_skill_and_mcp_are_bundled_without_placeholders():
    skill = (PLUGIN / "skills" / "athena-graphs" / "SKILL.md").read_text(encoding="utf-8")
    mcp = _json(PLUGIN / ".mcp.json")

    assert skill.startswith("---\nname: athena-graphs\n")
    assert "[TODO" not in skill
    assert "athena-graphs" in mcp["mcpServers"]
    assert "agentgraph-mcp" in mcp["mcpServers"]["athena-graphs"]["args"][-1]


def test_readme_keeps_the_tweet_and_agent_native_quick_start():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/petergraphs.png" in readme
    assert "Use Athena Graphs to implement" in readme
    assert "codex plugin marketplace add" in readme
    assert "claude plugin marketplace add" in readme
