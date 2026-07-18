"""A useful agent graph that works with every built-in backend."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

from .agent import Agent, AgentRequest, extract_json
from .checkpoint import Checkpointer
from .core import CompiledGraph, StateGraph
from .roles import (
    PLANNER_SYSTEM,
    REVIEWER_SYSTEM,
    REVISER_SYSTEM,
    WORKER_SYSTEM,
    planner_prompt,
    reviewer_prompt,
    reviser_prompt,
    worker_prompt,
)
from .types import END, START, RetryPolicy


def build_orchestrator_graph(
    agent: Agent,
    *,
    worker_count: int = 4,
    worker_attempts: int = 2,
    max_revisions: int = 2,
) -> StateGraph:
    """Build the plan -> parallel work -> review -> revision graph.

    The graph is returned uncompiled so callers can inspect its Mermaid diagram,
    add nodes/edges, or choose checkpoint and execution settings themselves.
    """

    if worker_count < 1:
        raise ValueError("worker_count must be at least 1")
    if worker_attempts < 1:
        raise ValueError("worker_attempts must be at least 1")
    if max_revisions < 0:
        raise ValueError("max_revisions cannot be negative")

    def plan(state: Mapping[str, Any]) -> dict[str, Any]:
        response = agent.run(
            AgentRequest(
                role="planner",
                system=PLANNER_SYSTEM,
                prompt=planner_prompt(str(state["goal"]), str(state["criteria"])),
                context=dict(state),
                expects_json=True,
            )
        )
        return {"tasks": _tasks(response.text), "revision": 0, "approved": False, "issues": []}

    def work(state: Mapping[str, Any]) -> dict[str, Any]:
        tasks = list(state.get("tasks", []))
        if not tasks:
            raise ValueError("planner produced no tasks")

        def one(task: dict[str, str]) -> dict[str, Any]:
            error = ""
            for attempt in range(1, worker_attempts + 1):
                try:
                    response = agent.run(
                        AgentRequest(
                            role="worker",
                            system=WORKER_SYSTEM,
                            prompt=worker_prompt(
                                str(state["goal"]), str(state["criteria"]), task
                            ),
                            context={**dict(state), "task_id": task["id"], "attempt": attempt},
                        )
                    )
                    return {
                        "id": task["id"],
                        "description": task["description"],
                        "ok": True,
                        "output": response.text,
                        "attempts": attempt,
                        "error": "",
                    }
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            return {
                "id": task["id"],
                "description": task["description"],
                "ok": False,
                "output": "",
                "attempts": worker_attempts,
                "error": error,
            }

        # Multiple coding-agent processes editing one checkout concurrently can
        # clobber each other. CliAgent exposes that shared directory as ``cwd``;
        # serialize only that case. API/content workers still fan out normally.
        safe_workers = 1 if getattr(agent, "cwd", None) else worker_count
        with ThreadPoolExecutor(max_workers=min(safe_workers, len(tasks)), thread_name_prefix="agentgraph-worker") as pool:
            results = list(pool.map(one, tasks))
        chunks = []
        failures = []
        for item in results:
            heading = f"### {item['id']}: {item['description']}"
            if item["ok"]:
                chunks.append(f"{heading}\n{item['output']}")
            else:
                failures.append(f"{item['id']}: {item['error']}")
                chunks.append(f"{heading}\n[FAILED] {item['error']}")
        return {
            "work_results": results,
            "final_output": "\n\n".join(chunks),
            "worker_failures": failures,
        }

    def review(state: Mapping[str, Any]) -> dict[str, Any]:
        failures = list(state.get("worker_failures", []))
        response = agent.run(
            AgentRequest(
                role="reviewer",
                system=REVIEWER_SYSTEM,
                prompt=reviewer_prompt(
                    str(state["goal"]),
                    str(state["criteria"]),
                    str(state.get("final_output", "")),
                    failures,
                ),
                context=dict(state),
                expects_json=True,
            )
        )
        value = extract_json(response.text)
        if not isinstance(value, dict):
            raise ValueError("reviewer response must be a JSON object")
        issues = [str(item) for item in value.get("issues", [])]
        approved = bool(value.get("approved", False)) and not failures
        if failures:
            issues.extend(failures)
        return {
            "approved": approved,
            "issues": issues,
            "review_summary": str(value.get("summary", "")),
        }

    def revise(state: Mapping[str, Any]) -> dict[str, Any]:
        response = agent.run(
            AgentRequest(
                role="reviser",
                system=REVISER_SYSTEM,
                prompt=reviser_prompt(
                    str(state["goal"]), str(state["criteria"]), list(state.get("issues", []))
                ),
                context=dict(state),
                expects_json=True,
            )
        )
        return {
            "tasks": _tasks(response.text),
            "revision": int(state.get("revision", 0)) + 1,
        }

    def exhausted(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "revision_budget_exhausted",
            "approved": False,
        }

    def after_review(state: Mapping[str, Any]) -> str:
        if state.get("approved"):
            return "approved"
        if int(state.get("revision", 0)) < max_revisions:
            return "revise"
        return "exhausted"

    graph = StateGraph()
    graph.add_node("plan", plan, retry=RetryPolicy(max_attempts=2))
    graph.add_node("work", work)
    graph.add_node("review", review, retry=RetryPolicy(max_attempts=2))
    graph.add_node("revise", revise, retry=RetryPolicy(max_attempts=2))
    graph.add_node("exhausted", exhausted)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "work")
    graph.add_edge("work", "review")
    graph.add_conditional_edges(
        "review",
        after_review,
        {"approved": END, "revise": "revise", "exhausted": "exhausted"},
    )
    graph.add_edge("revise", "work")
    graph.add_edge("exhausted", END)
    return graph


def create_orchestrator_graph(
    agent: Agent,
    *,
    worker_count: int = 4,
    worker_attempts: int = 2,
    max_revisions: int = 2,
    checkpointer: Checkpointer | None = None,
    parallel: bool = True,
) -> CompiledGraph:
    builder = build_orchestrator_graph(
        agent,
        worker_count=worker_count,
        worker_attempts=worker_attempts,
        max_revisions=max_revisions,
    )
    # plan/work/review + three nodes per revision, with headroom for extensions.
    return builder.compile(
        checkpointer=checkpointer,
        max_steps=5 + max_revisions * 3,
        parallel=parallel,
    )


def _tasks(text: str) -> list[dict[str, str]]:
    value = extract_json(text)
    if not isinstance(value, list):
        raise ValueError("task response must be a JSON array")
    tasks: list[dict[str, str]] = []
    used: set[str] = set()
    for index, item in enumerate(value, 1):
        if isinstance(item, str):
            task_id = f"task-{index}"
            description = item.strip()
        elif isinstance(item, dict):
            task_id = str(item.get("id") or f"task-{index}").strip()
            description = str(item.get("description") or "").strip()
        else:
            raise ValueError(f"task {index} must be a string or object")
        if not task_id or not description:
            raise ValueError(f"task {index} needs a non-empty id and description")
        if task_id in used:
            raise ValueError(f"duplicate task id {task_id!r}")
        used.add(task_id)
        tasks.append({"id": task_id, "description": description})
    if not tasks:
        raise ValueError("task array cannot be empty")
    return tasks
