"""MCP front end for durable, declarative Athena Graphs runs."""

from __future__ import annotations

from typing import Any

from .adapters import BACKENDS
from .runs import GraphRunManager
from .service import doctor_impl
from .spec import normalize_topology, topology_mermaid


_MANAGER = GraphRunManager()


def graph_start_impl(
    goal: str,
    success_criteria: str = "The requested goal is complete, correct, and verified.",
    *,
    topology: dict[str, Any] | None = None,
    backend: str = "auto",
    caller_agent: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    skip_permissions: bool = False,
    max_steps: int | None = None,
    max_seconds: float | None = None,
    parallel: bool | None = None,
    manager: GraphRunManager | None = None,
) -> dict[str, Any]:
    return (manager or _MANAGER).start(
        goal=goal,
        success_criteria=success_criteria,
        topology=topology,
        backend=backend,
        caller_agent=caller_agent,
        cwd=cwd,
        model=model,
        timeout=timeout,
        skip_permissions=skip_permissions,
        max_steps=max_steps,
        max_seconds=max_seconds,
        parallel=parallel,
    )


def build_server(manager: GraphRunManager | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("MCP support requires: pip install 'athena-graphs[mcp]'") from exc

    runs = manager or _MANAGER
    server = FastMCP("athena-graphs")

    @server.tool()
    def graph_start(
        goal: str,
        success_criteria: str = "The requested goal is complete, correct, and verified.",
        topology: dict[str, Any] | None = None,
        backend: str = "auto",
        caller_agent: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        skip_permissions: bool = False,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        parallel: bool | None = None,
    ) -> dict[str, Any]:
        """Start a detached graph. Supply a declarative topology or use the default."""

        return graph_start_impl(
            goal,
            success_criteria,
            topology=topology,
            backend=backend,
            caller_agent=caller_agent,
            cwd=cwd,
            model=model,
            timeout=timeout,
            skip_permissions=skip_permissions,
            max_steps=max_steps,
            max_seconds=max_seconds,
            parallel=parallel,
            manager=runs,
        )

    @server.tool()
    def graph_status(run_id: str) -> dict[str, Any]:
        """Read current phase, step, running state, errors, and pending interrupts."""

        return runs.status(run_id)

    @server.tool()
    def graph_tail(run_id: str, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        """Read graph events since cursor; pass the returned cursor to the next call."""

        return runs.tail(run_id, cursor=cursor, limit=limit)

    @server.tool()
    def graph_resume(run_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        """Resume a graph interrupted for human input by merging answers into state."""

        return runs.resume(run_id, answers)

    @server.tool()
    def graph_result(run_id: str, wait: bool = False, timeout: float = 0.0) -> dict[str, Any]:
        """Return the final structured result, or current readiness if still running."""

        return runs.result(run_id, wait=wait, timeout=min(max(timeout, 0.0), 60.0))

    @server.tool()
    def graph_diagram(
        topology: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> str:
        """Render a proposed topology or an existing run as Mermaid source."""

        if run_id:
            return runs.diagram(run_id)
        return topology_mermaid(topology)

    @server.tool()
    def graph_validate(topology: dict[str, Any]) -> dict[str, Any]:
        """Validate a declarative topology before starting it."""

        normalized = normalize_topology(topology)
        return {"valid": True, "topology": normalized, "diagram": topology_mermaid(normalized)}

    @server.tool()
    def graph_list(limit: int = 50) -> list[dict[str, Any]]:
        """List recent durable runs known to this MCP server."""

        return runs.list(limit=min(max(limit, 1), 200))

    @server.tool()
    def list_backends() -> list[str]:
        """List available model API and coding-agent CLI backend identifiers."""

        return list(BACKENDS)

    @server.tool()
    def doctor(cwd: str | None = None) -> dict[str, Any]:
        """Check the target directory and backend prerequisites."""

        return doctor_impl(cwd)

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
