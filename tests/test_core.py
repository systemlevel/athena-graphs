from __future__ import annotations

import operator
import pytest

from agentgraph import (
    END,
    START,
    Command,
    GraphValidationError,
    JsonFileCheckpointer,
    MemoryCheckpointer,
    RetryPolicy,
    StateGraph,
    interrupt,
)


def test_linear_graph_reaches_end_and_updates_state():
    graph = StateGraph()
    graph.add_node("double", lambda state: {"value": state["value"] * 2})
    graph.add_edge(START, "double")
    graph.add_edge("double", END)

    result = graph.compile().invoke({"value": 3})

    assert result.completed
    assert result.state["value"] == 6
    assert result.steps == 1
    assert result.stop_reason == "end"


def test_conditional_route_can_loop():
    graph = StateGraph()
    graph.add_node("increment", lambda state: {"count": state.get("count", 0) + 1})
    graph.add_edge(START, "increment")
    graph.add_conditional_edges(
        "increment",
        lambda state: "again" if state["count"] < 3 else "done",
        {"again": "increment", "done": END},
    )

    result = graph.compile().invoke({})

    assert result.completed
    assert result.state["count"] == 3
    assert [step.active for step in result.history] == [
        ["increment"], ["increment"], ["increment"]
    ]


def test_command_goto_overrides_declared_edge():
    graph = StateGraph()
    graph.add_node("choose", lambda _state: Command(goto="right"))
    graph.add_node("left", lambda _state: {"path": "left"})
    graph.add_node("right", lambda _state: {"path": "right"})
    graph.add_edge(START, "choose")
    graph.add_edge("choose", "left")
    graph.add_edge("left", END)
    graph.add_edge("right", END)

    result = graph.compile().invoke({})

    assert result.state["path"] == "right"


def test_fanout_runs_in_one_superstep_and_join_waits_for_all_sources():
    graph = StateGraph()
    graph.set_reducer("items", operator.add)
    graph.add_node("fan", lambda _state: {})
    graph.add_node("a", lambda _state: {"items": ["a"]})
    graph.add_node("b", lambda _state: {"items": ["b"]})
    graph.add_node("join", lambda state: {"joined": "".join(sorted(state["items"]))})
    graph.add_edge(START, "fan")
    graph.add_edge("fan", "a")
    graph.add_edge("fan", "b")
    graph.add_edge(["a", "b"], "join")
    graph.add_edge("join", END)

    result = graph.compile().invoke({})

    assert result.completed
    assert result.history[1].active == ["a", "b"]
    assert result.history[2].active == ["join"]
    assert result.state["joined"] == "ab"


def test_parallel_conflict_is_reported_without_a_reducer():
    graph = StateGraph()
    graph.add_node("fan", lambda _state: {})
    graph.add_node("a", lambda _state: {"value": 1})
    graph.add_node("b", lambda _state: {"value": 2})
    graph.add_edge(START, "fan")
    graph.add_edge("fan", "a")
    graph.add_edge("fan", "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)

    result = graph.compile().invoke({})

    assert result.status == "failed"
    assert result.stop_reason == "state_merge_error"
    assert "register a reducer" in result.error


def test_reducer_accumulates_across_steps_as_well_as_parallel_writes():
    graph = StateGraph()
    graph.set_reducer("messages", operator.add)
    graph.add_node("one", lambda _state: {"messages": ["one"]})
    graph.add_node("two", lambda _state: {"messages": ["two"]})
    graph.add_edge(START, "one")
    graph.add_edge("one", "two")
    graph.add_edge("two", END)

    result = graph.compile().invoke({"messages": ["start"]})

    assert result.state["messages"] == ["start", "one", "two"]


def test_retry_policy_recovers_locally():
    attempts = 0

    def flaky(_state):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    graph = StateGraph()
    graph.add_node("flaky", flaky, retry=RetryPolicy(max_attempts=3))
    graph.add_edge(START, "flaky")
    graph.add_edge("flaky", END)

    result = graph.compile().invoke({})

    assert result.completed
    assert result.history[0].nodes[0].attempts == 3


def test_exhausted_node_can_take_an_error_edge():
    graph = StateGraph()
    graph.add_node("fail", lambda _state: 1 / 0, on_error="recover")
    graph.add_node("recover", lambda state: {"recovered": state["failed_node"]})
    graph.add_edge(START, "fail")
    graph.add_edge("fail", END)
    graph.add_edge("recover", END)

    result = graph.compile().invoke({})

    assert result.completed
    assert result.state["recovered"] == "fail"
    assert "ZeroDivisionError" in result.state["last_error"]


def test_max_steps_stops_a_cycle():
    graph = StateGraph()
    graph.add_node("forever", lambda _state: {})
    graph.add_edge(START, "forever")
    graph.add_edge("forever", "forever")

    result = graph.compile(max_steps=3).invoke({})

    assert result.status == "failed"
    assert result.steps == 3
    assert result.stop_reason == "max_steps"


def test_interrupt_and_resume_with_memory_checkpoint():
    store = MemoryCheckpointer()

    def approve(state):
        if "answer" not in state:
            return interrupt("Approve?", data={"draft": state["draft"]})
        return {"verdict": state["answer"]}

    graph = StateGraph()
    graph.add_node("draft", lambda _state: {"draft": "hello"})
    graph.add_node("approve", approve)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "approve")
    graph.add_edge("approve", END)
    app = graph.compile(checkpointer=store)

    paused = app.invoke({}, thread_id="thread-1")
    finished = app.resume("thread-1", {"answer": "yes"})

    assert paused.status == "interrupted"
    assert paused.interrupts[0].data == {"draft": "hello"}
    assert finished.completed
    assert finished.state["verdict"] == "yes"
    assert store.load("thread-1") is None


def test_json_checkpoint_survives_a_new_graph_instance(tmp_path):
    store = JsonFileCheckpointer(tmp_path)

    def gate(state):
        return {"seen": state["answer"]} if "answer" in state else interrupt("Answer")

    builder = StateGraph()
    builder.add_node("gate", gate)
    builder.add_edge(START, "gate")
    builder.add_edge("gate", END)
    builder.compile(checkpointer=store).invoke({"x": 1}, thread_id="durable")

    finished = builder.compile(checkpointer=JsonFileCheckpointer(tmp_path)).resume(
        "durable", {"answer": 42}
    )

    assert finished.completed
    assert finished.state["seen"] == 42


def test_event_observer_sees_nodes_and_cannot_break_execution():
    kinds = []

    def observer(event):
        kinds.append(event.kind)
        if event.kind == "node_started":
            raise RuntimeError("sink is broken")

    graph = StateGraph()
    graph.add_node("node", lambda _state: {})
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    result = graph.compile().invoke({}, observer=observer)

    assert result.completed
    assert "graph_started" in kinds
    assert "node_started" in kinds
    assert "graph_finished" in kinds


def test_validation_catches_missing_start_and_unknown_targets():
    graph = StateGraph()
    graph.add_node("only", lambda _state: {})
    with pytest.raises(GraphValidationError, match="START"):
        graph.compile()

    graph.add_edge(START, "missing")
    with pytest.raises(GraphValidationError, match="unknown node"):
        graph.compile()


def test_mermaid_contains_branches_routes_and_joins():
    graph = StateGraph()
    for name in ("a", "b", "c", "join"):
        graph.add_node(name, lambda _state: {})
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_conditional_edges("a", lambda _state: "c", {"take c": "c"})
    graph.add_edge(["b", "c"], "join")
    graph.add_edge("join", END)

    diagram = graph.to_mermaid()

    assert "flowchart TD" in diagram
    assert "{{all}}" in diagram
    assert "take c" in diagram
