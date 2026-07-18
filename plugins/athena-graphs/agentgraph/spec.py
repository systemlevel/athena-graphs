"""Compile a JSON-friendly agent graph topology into the core runtime."""

from __future__ import annotations

import json
from string import Template
from typing import Any, Mapping

from .agent import Agent, AgentRequest, extract_json
from .checkpoint import Checkpointer
from .core import CompiledGraph, StateGraph
from .types import END, START, RetryPolicy, interrupt


def default_topology() -> dict[str, Any]:
    """A useful graph for callers that do not provide an explicit topology."""

    return {
        "name": "analyze-execute-review",
        "max_steps": 12,
        "nodes": [
            {
                "id": "analyze",
                "kind": "agent",
                "role": "analyst",
                "system": (
                    "Analyze the goal before implementation. Identify constraints, risks, "
                    "dependencies, and a concrete approach. Do not edit files."
                ),
                "prompt": "GOAL:\n$goal\n\nSUCCESS CRITERIA:\n$success_criteria",
                "output_key": "analysis",
            },
            {
                "id": "execute",
                "kind": "agent",
                "role": "worker",
                "system": (
                    "Complete the requested work using your tools. Produce the deliverable, "
                    "inspect existing work first, and verify the result when practical."
                ),
                "prompt": "GOAL:\n$goal\n\nSUCCESS CRITERIA:\n$success_criteria",
                "output_key": "work_output",
            },
            {
                "id": "review",
                "kind": "agent",
                "role": "reviewer",
                "system": (
                    "Review the work against every success criterion. Return only JSON: "
                    '{"approved": bool, "issues": ["actionable issue"], "summary": "verdict"}'
                ),
                "prompt": (
                    "GOAL:\n$goal\n\nSUCCESS CRITERIA:\n$success_criteria\n\n"
                    "ANALYSIS:\n$analysis\n\nWORK OUTPUT:\n$work_output"
                ),
                "response": "json",
                "merge": True,
                "retry": {"max_attempts": 2},
            },
            {
                "id": "revise",
                "kind": "agent",
                "role": "worker",
                "system": (
                    "Resolve every review issue. Preserve correct existing work, use your "
                    "tools, verify the revision, and return the updated deliverable."
                ),
                "prompt": (
                    "GOAL:\n$goal\n\nSUCCESS CRITERIA:\n$success_criteria\n\n"
                    "REVIEW ISSUES:\n$issues\n\nPREVIOUS OUTPUT:\n$work_output"
                ),
                "output_key": "work_output",
            },
        ],
        "edges": [
            {"from": START, "to": "analyze"},
            {"from": START, "to": "execute"},
            {"from": ["analyze", "execute"], "to": "review"},
            {"from": "review", "to": END, "when": {"key": "approved", "equals": True}},
            {"from": "review", "to": "revise", "when": {"key": "approved", "equals": False}},
            {"from": "revise", "to": "review"},
        ],
    }


def compile_topology(
    topology: Mapping[str, Any] | None,
    agent: Agent,
    *,
    checkpointer: Checkpointer | None = None,
    max_steps: int | None = None,
    max_seconds: float | None = None,
    parallel: bool = True,
) -> CompiledGraph:
    graph, normalized = build_topology(topology, agent)
    return graph.compile(
        checkpointer=checkpointer,
        max_steps=max_steps or int(normalized.get("max_steps", 100)),
        max_seconds=max_seconds,
        parallel=parallel,
    )


def topology_mermaid(topology: Mapping[str, Any] | None) -> str:
    # Diagram generation never calls the agent, so a deliberately unreachable
    # placeholder is enough to compile node closures.
    class _NoAgent:
        def run(self, _request):
            raise RuntimeError("diagram nodes cannot execute")

    graph, _ = build_topology(topology, _NoAgent())
    return graph.to_mermaid()


def normalize_topology(topology: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(topology or default_topology())
    nodes = source.get("nodes")
    edges = source.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("topology.nodes must be a non-empty array")
    if not isinstance(edges, list) or not edges:
        raise ValueError("topology.edges must be a non-empty array")
    source["nodes"] = [dict(item) if isinstance(item, Mapping) else item for item in nodes]
    source["edges"] = [dict(item) if isinstance(item, Mapping) else item for item in edges]
    return source


def build_topology(
    topology: Mapping[str, Any] | None,
    agent: Agent,
) -> tuple[StateGraph, dict[str, Any]]:
    normalized = normalize_topology(topology)
    graph = StateGraph()
    used: set[str] = set()

    for raw in normalized["nodes"]:
        if not isinstance(raw, Mapping):
            raise ValueError("every topology node must be an object")
        node = dict(raw)
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            raise ValueError("every topology node needs a non-empty id")
        if node_id in used:
            raise ValueError(f"duplicate topology node id {node_id!r}")
        used.add(node_id)
        action = _node_action(node, agent)
        retry_value = node.get("retry", {})
        if retry_value is None:
            retry_value = {}
        if not isinstance(retry_value, Mapping):
            raise ValueError(f"node {node_id!r} retry must be an object")
        retry = RetryPolicy(
            max_attempts=int(retry_value.get("max_attempts", 1)),
            initial_delay=float(retry_value.get("initial_delay", 0.0)),
            backoff=float(retry_value.get("backoff", 2.0)),
        )
        on_error = node.get("on_error")
        graph.add_node(
            node_id,
            action,
            retry=retry,
            on_error=str(on_error) if on_error is not None else None,
        )

    reducers = normalized.get("reducers", {})
    if reducers:
        if not isinstance(reducers, Mapping):
            raise ValueError("topology.reducers must be an object")
        for key, name in reducers.items():
            graph.set_reducer(str(key), _reducer(str(name)))

    for raw in normalized["edges"]:
        if not isinstance(raw, Mapping):
            raise ValueError("every topology edge must be an object")
        if "from" not in raw or "to" not in raw:
            raise ValueError("every topology edge needs 'from' and 'to'")
        source = raw["from"]
        if isinstance(source, list):
            source = [str(item) for item in source]
        else:
            source = str(source)
        graph.add_edge(
            source,
            str(raw["to"]),
            when=_condition(raw.get("when")) if "when" in raw else None,
            label=str(raw.get("label", "")) or _condition_label(raw.get("when")),
        )
    # Compile once here to surface structural errors during validation, before a
    # detached run is accepted and assigned an id.
    graph.compile(max_steps=int(normalized.get("max_steps", 100)))
    return graph, normalized


def _node_action(node: dict[str, Any], agent: Agent):
    kind = str(node.get("kind", "agent"))
    node_id = str(node["id"])
    if kind == "agent":
        system = str(node.get("system", "Complete this graph node carefully."))
        prompt = str(node.get("prompt", "$goal"))
        role = str(node.get("role", node_id))
        response_type = str(node.get("response", "text"))
        if response_type not in ("text", "json"):
            raise ValueError(f"node {node_id!r} response must be text or json")
        merge = bool(node.get("merge", False))
        output_key = str(node.get("output_key", node_id))

        def run_agent(state: Mapping[str, Any]) -> dict[str, Any]:
            rendered = _template(prompt, state)
            response = agent.run(
                AgentRequest(
                    role=role,
                    system=system,
                    prompt=rendered,
                    context=dict(state),
                    expects_json=response_type == "json",
                )
            )
            if response_type == "text":
                return {output_key: response.text}
            value = extract_json(response.text)
            if merge:
                if not isinstance(value, Mapping):
                    raise ValueError(f"node {node_id!r} must return a JSON object when merge=true")
                return dict(value)
            return {output_key: value}

        return run_agent

    if kind == "human":
        question = str(node.get("question", "Please review this graph step."))
        answer_key = str(node.get("answer_key", f"{node_id}_answer"))
        data_keys = node.get("data_keys", [])
        if not isinstance(data_keys, list):
            raise ValueError(f"node {node_id!r} data_keys must be an array")

        def ask_human(state: Mapping[str, Any]):
            if answer_key not in state:
                data = {str(key): state.get(str(key)) for key in data_keys}
                return interrupt(_template(question, state), data=data)
            return {f"{node_id}_completed": True}

        return ask_human

    if kind == "set":
        update = node.get("update", {})
        if not isinstance(update, Mapping):
            raise ValueError(f"node {node_id!r} update must be an object")

        def set_state(state: Mapping[str, Any]) -> dict[str, Any]:
            return {
                str(key): _template(value, state) if isinstance(value, str) else value
                for key, value in update.items()
            }

        return set_state

    raise ValueError(f"node {node_id!r} has unsupported kind {kind!r}")


def _condition(value: Any):
    if not isinstance(value, Mapping):
        raise ValueError("edge.when must be an object")
    rule = dict(value)

    def evaluate(state: Mapping[str, Any]) -> bool:
        return _evaluate_rule(rule, state)

    return evaluate


def _evaluate_rule(rule: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    if "all" in rule:
        values = rule["all"]
        if not isinstance(values, list):
            raise ValueError("condition.all must be an array")
        return all(_evaluate_rule(item, state) for item in values)
    if "any" in rule:
        values = rule["any"]
        if not isinstance(values, list):
            raise ValueError("condition.any must be an array")
        return any(_evaluate_rule(item, state) for item in values)
    if "not" in rule:
        value = rule["not"]
        if not isinstance(value, Mapping):
            raise ValueError("condition.not must be an object")
        return not _evaluate_rule(value, state)

    key = str(rule.get("key", ""))
    if not key:
        raise ValueError("condition needs a key")
    exists, actual = _lookup(state, key)
    if "exists" in rule:
        return exists is bool(rule["exists"])
    if "equals" in rule:
        return exists and actual == rule["equals"]
    if "not_equals" in rule:
        return (not exists) or actual != rule["not_equals"]
    if rule.get("truthy") is True:
        return exists and bool(actual)
    if rule.get("falsy") is True:
        return (not exists) or not bool(actual)
    if "contains" in rule:
        try:
            return exists and rule["contains"] in actual
        except TypeError:
            return False
    raise ValueError("condition needs equals, not_equals, exists, truthy, falsy, or contains")


def _lookup(state: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _condition_label(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    key = value.get("key")
    if key is None:
        return "condition"
    for operation in ("equals", "not_equals", "exists", "truthy", "falsy", "contains"):
        if operation in value:
            return f"{key} {operation} {value[operation]}"
    return str(key)


def _template(text: str, state: Mapping[str, Any]) -> str:
    values = {str(key): _string(value) for key, value in state.items()}
    return Template(text).safe_substitute(values)


def _string(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _reducer(name: str):
    if name == "append":
        return lambda left, right: [*left, *right]
    if name == "sum":
        return lambda left, right: left + right
    if name == "merge":
        return lambda left, right: {**left, **right}
    raise ValueError(f"unsupported reducer {name!r}; use append, sum, or merge")
