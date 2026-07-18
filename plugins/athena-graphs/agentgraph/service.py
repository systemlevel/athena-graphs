"""JSON-friendly service functions used by both CLI and MCP."""

from __future__ import annotations

import os
import shutil
from typing import Any

from .adapters import BACKENDS, build_agent
from .prebuilt import build_orchestrator_graph, create_orchestrator_graph


def run_graph_impl(
    goal: str,
    criteria: str = "The requested goal is complete and correct.",
    *,
    backend: str = "mock",
    caller_agent: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    skip_permissions: bool = False,
    max_revisions: int = 2,
    worker_count: int = 4,
) -> dict[str, Any]:
    agent = build_agent(
        backend,
        caller_agent=caller_agent,
        cwd=cwd,
        model=model,
        timeout=timeout,
        skip_permissions=skip_permissions,
    )
    graph = create_orchestrator_graph(
        agent,
        worker_count=worker_count,
        max_revisions=max_revisions,
    )
    result = graph.invoke({"goal": goal, "criteria": criteria})
    payload = result.to_dict()
    approved = bool(result.state.get("approved"))
    payload["approved"] = approved
    payload["summary"] = (
        f"approved in {result.steps} graph step(s)"
        if approved
        else f"stopped after {result.steps} graph step(s): {result.stop_reason}"
    )
    return payload


def diagram_impl() -> str:
    from .adapters import MockAgent

    return build_orchestrator_graph(MockAgent()).to_mermaid()


def doctor_impl(cwd: str | None = None) -> dict[str, Any]:
    target = os.path.abspath(cwd or os.getcwd())
    executables = {
        "claude_code": "claude",
        "codex": "codex",
        "opencode": "opencode",
        "aider": "aider",
        "grok_build": "grok",
    }
    backends: dict[str, Any] = {
        "mock": {"available": True, "kind": "built-in"},
        "claude": {
            "available": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "kind": "api",
            "env": "ANTHROPIC_API_KEY",
        },
        "grok_api": {
            "available": bool(os.environ.get("XAI_API_KEY")),
            "kind": "api",
            "env": "XAI_API_KEY",
        },
    }
    for backend, executable in executables.items():
        path = shutil.which(executable)
        backends[backend] = {
            "available": path is not None,
            "kind": "cli",
            "executable": executable,
            "path": path,
        }
    return {
        "ok": os.path.isdir(target),
        "cwd": {
            "path": target,
            "exists": os.path.exists(target),
            "directory": os.path.isdir(target),
            "writable": os.access(target, os.W_OK),
        },
        "backends": backends,
        "supported": list(BACKENDS),
    }
