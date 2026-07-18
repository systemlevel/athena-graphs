from __future__ import annotations

import pytest

from agentgraph.adapters import MockAgent
from agentgraph.spec import compile_topology, default_topology, topology_mermaid


def test_default_topology_branches_joins_revises_and_completes():
    app = compile_topology(default_topology(), MockAgent(accept_on_review=2))

    result = app.invoke({"goal": "g", "success_criteria": "c"})

    assert result.completed
    assert result.state["approved"] is True
    assert result.history[0].active == ["analyze", "execute"]
    assert [step.active for step in result.history[1:]] == [
        ["review"], ["revise"], ["review"]
    ]


def test_json_agent_node_can_merge_for_conditional_routing():
    topology = {
        "nodes": [
            {
                "id": "judge",
                "kind": "agent",
                "response": "json",
                "merge": True,
            }
        ],
        "edges": [
            {"from": "__start__", "to": "judge"},
            {"from": "judge", "to": "__end__", "when": {"key": "approved", "equals": True}},
        ],
    }

    result = compile_topology(topology, MockAgent()).invoke({"goal": "g"})

    assert result.completed
    assert result.state["approved"] is True


def test_nested_condition_and_set_template():
    topology = {
        "nodes": [
            {"id": "seed", "kind": "set", "update": {"review": {"approved": True}}},
            {"id": "message", "kind": "set", "update": {"text": "Goal: $goal"}},
        ],
        "edges": [
            {"from": "__start__", "to": "seed"},
            {"from": "seed", "to": "message", "when": {"key": "review.approved", "truthy": True}},
            {"from": "message", "to": "__end__"},
        ],
    }

    result = compile_topology(topology, MockAgent()).invoke({"goal": "ship"})

    assert result.state["text"] == "Goal: ship"


def test_invalid_topology_is_rejected_before_execution():
    with pytest.raises(ValueError, match="non-empty array"):
        compile_topology({"nodes": [], "edges": []}, MockAgent())


def test_topology_mermaid_does_not_call_a_backend():
    diagram = topology_mermaid(default_topology())

    assert "flowchart TD" in diagram
    assert "{{all}}" in diagram
    assert "approved equals True" in diagram
