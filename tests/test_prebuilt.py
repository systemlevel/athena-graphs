from __future__ import annotations

from agentgraph.adapters import MockAgent
from agentgraph.prebuilt import build_orchestrator_graph, create_orchestrator_graph
from agentgraph.service import diagram_impl, doctor_impl, run_graph_impl


def test_prebuilt_graph_revises_then_approves():
    app = create_orchestrator_graph(MockAgent(accept_on_review=2), max_revisions=2)

    result = app.invoke({"goal": "write it", "criteria": "complete"})

    assert result.completed
    assert result.state["approved"] is True
    assert result.state["revision"] == 1
    assert result.state["final_output"]
    assert [step.active[0] for step in result.history] == [
        "plan", "work", "review", "revise", "work", "review"
    ]


def test_prebuilt_graph_exhausts_revision_budget_without_false_approval():
    app = create_orchestrator_graph(MockAgent(accept_on_review=99), max_revisions=1)

    result = app.invoke({"goal": "write it", "criteria": "complete"})

    assert result.completed  # control-flow graph reached its declared END
    assert result.state["approved"] is False
    assert result.state["outcome"] == "revision_budget_exhausted"


def test_worker_failure_is_visible_and_blocks_review_approval():
    def fail(_request):
        raise RuntimeError("worker exploded")

    app = create_orchestrator_graph(
        MockAgent(accept_on_review=1, worker_fn=fail),
        worker_attempts=2,
        max_revisions=0,
    )

    result = app.invoke({"goal": "write it", "criteria": "complete"})

    assert result.state["approved"] is False
    assert result.state["worker_failures"]
    assert result.state["work_results"][0]["attempts"] == 2


def test_service_payload_is_json_friendly_and_readable():
    payload = run_graph_impl("goal", "criteria", backend="mock")

    assert payload["approved"] is True
    assert payload["completed"] is True
    assert "approved in" in payload["summary"]


def test_diagram_and_doctor_surfaces(tmp_path):
    diagram = diagram_impl()
    doctor = doctor_impl(str(tmp_path))

    assert "flowchart TD" in diagram
    assert "review" in diagram
    assert doctor["ok"] is True
    assert doctor["backends"]["mock"]["available"] is True
    assert "codex" in doctor["backends"]


def test_builder_can_be_extended_before_compile():
    builder = build_orchestrator_graph(MockAgent(accept_on_review=1))
    assert "plan" in builder.to_mermaid()
