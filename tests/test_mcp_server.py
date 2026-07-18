from __future__ import annotations

import pytest


def test_mcp_server_constructs_when_sdk_is_available():
    pytest.importorskip("mcp")
    from agentgraph.mcp_server import build_server

    assert build_server().name == "athena-graphs"


def test_graph_start_impl_uses_injected_run_manager(tmp_path):
    from agentgraph.mcp_server import graph_start_impl
    from agentgraph.runs import GraphRunManager

    manager = GraphRunManager(tmp_path)
    started = graph_start_impl("goal", "criteria", backend="mock", manager=manager)

    assert started["status"] == "running"
    assert manager.status(started["run_id"])["run_id"] == started["run_id"]
