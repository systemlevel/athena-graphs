from __future__ import annotations

import time

from agentgraph.runs import GraphRunManager


def _finished(manager: GraphRunManager, run_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(run_id)
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_detached_run_can_be_polled_tailed_and_read(tmp_path):
    manager = GraphRunManager(tmp_path)

    started = manager.start(
        goal="g",
        success_criteria="c",
        topology=None,
        backend="mock",
        caller_agent="codex",
    )
    status = _finished(manager, started["run_id"])
    tail = manager.tail(started["run_id"])
    result = manager.result(started["run_id"])

    assert status["status"] == "completed"
    assert result["state"]["approved"] is True
    assert any(event["kind"] == "node_finished" for event in tail["events"])
    assert tail["cursor"] == len(tail["events"])
    assert manager.list()[0]["run_id"] == started["run_id"]


def test_human_interrupt_is_durable_and_resumable(tmp_path):
    manager = GraphRunManager(tmp_path)
    topology = {
        "nodes": [
            {"id": "draft", "kind": "set", "update": {"draft": "$goal"}},
            {
                "id": "approval",
                "kind": "human",
                "question": "Approve $draft?",
                "answer_key": "approved",
                "data_keys": ["draft"],
            },
        ],
        "edges": [
            {"from": "__start__", "to": "draft"},
            {"from": "draft", "to": "approval"},
            {"from": "approval", "to": "__end__"},
        ],
    }

    started = manager.start(
        goal="release",
        success_criteria="approved",
        topology=topology,
        backend="mock",
    )
    paused = _finished(manager, started["run_id"])
    assert paused["status"] == "interrupted"
    assert paused["interrupts"][0]["data"] == {"draft": "release"}

    manager.resume(started["run_id"], {"approved": True})
    finished = _finished(manager, started["run_id"])
    result = manager.result(started["run_id"])

    assert finished["status"] == "completed"
    assert result["state"]["approved"] is True


def test_coding_backend_with_shared_cwd_defaults_to_sequential(monkeypatch, tmp_path):
    manager = GraphRunManager(tmp_path / "home")
    # Start never constructs the CLI in the foreground, so stop after checking
    # the returned execution policy; the daemon thread may fail harmlessly.
    monkeypatch.setattr(manager, "_execute", lambda *_args: None)

    started = manager.start(
        goal="g",
        success_criteria="c",
        topology=None,
        backend="codex",
        cwd=str(tmp_path),
    )

    assert started["parallel"] is False
