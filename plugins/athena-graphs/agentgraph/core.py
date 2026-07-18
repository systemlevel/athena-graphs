"""Deterministic directed-graph runtime for agent workflows.

The graph owns control flow. Nodes own work. State is the only communication
channel between them, which keeps branching, joins, retries, and loops visible
and testable instead of burying those decisions in a prompt.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from .checkpoint import Checkpointer
from .types import (
    END,
    START,
    Checkpoint,
    Command,
    Condition,
    GraphError,
    GraphEvent,
    GraphResult,
    GraphValidationError,
    Interrupt,
    NodeAction,
    NodeTrace,
    Observer,
    Reducer,
    RetryPolicy,
    Router,
    State,
    StateConflictError,
    StepTrace,
)


@dataclass
class _Node:
    action: NodeAction
    retry: RetryPolicy
    on_error: str | None = None


@dataclass
class _Edge:
    key: str
    sources: tuple[str, ...]
    target: str
    condition: Condition | None = None
    label: str = ""


@dataclass
class _Route:
    source: str
    router: Router
    paths: dict[Any, str] | None = None
    labels: dict[Any, str] = field(default_factory=dict)


@dataclass
class _NodeRun:
    trace: NodeTrace
    command: Command | None = None
    interrupt: Interrupt | None = None


class StateGraph:
    """Builder for a stateful directed graph.

    A list of edge sources declares a join: the target becomes ready only after
    every source has arrived. Multiple ordinary edges from one node fan out and
    run in the same parallel superstep.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, _Node] = {}
        self._edges: list[_Edge] = []
        self._routes: list[_Route] = []
        self._reducers: dict[str, Reducer] = {}

    def add_node(
        self,
        name: str,
        action: NodeAction,
        *,
        retry: RetryPolicy | None = None,
        on_error: str | None = None,
    ) -> "StateGraph":
        self._validate_name(name)
        if name in self._nodes:
            raise GraphValidationError(f"duplicate node {name!r}")
        if not callable(action):
            raise TypeError("node action must be callable")
        self._nodes[name] = _Node(action, retry or RetryPolicy(), on_error)
        return self

    def add_edge(
        self,
        source: str | Iterable[str],
        target: str,
        *,
        when: Condition | None = None,
        label: str = "",
    ) -> "StateGraph":
        sources = (source,) if isinstance(source, str) else tuple(source)
        if not sources:
            raise GraphValidationError("an edge needs at least one source")
        if len(set(sources)) != len(sources):
            raise GraphValidationError("join sources must be unique")
        if START in sources and len(sources) > 1:
            raise GraphValidationError("START cannot be one source of a join")
        if END in sources:
            raise GraphValidationError("END cannot have outgoing edges")
        key = f"edge-{len(self._edges)}"
        self._edges.append(_Edge(key, sources, target, when, label))
        return self

    def add_conditional_edges(
        self,
        source: str,
        router: Router,
        paths: Mapping[Any, str] | None = None,
        *,
        labels: Mapping[Any, str] | None = None,
    ) -> "StateGraph":
        """Route dynamically after ``source``.

        The router may return one path key/node, an iterable of them (fan-out),
        or ``None`` (no path). With ``paths``, return values are looked up in that
        mapping; without it, return values must be literal node names or ``END``.
        """

        if source == END:
            raise GraphValidationError("END cannot have outgoing routes")
        if not callable(router):
            raise TypeError("router must be callable")
        self._routes.append(
            _Route(source, router, dict(paths) if paths is not None else None, dict(labels or {}))
        )
        return self

    def set_reducer(self, key: str, reducer: Reducer) -> "StateGraph":
        """Merge repeated/parallel updates for a state key deterministically."""

        if not key:
            raise ValueError("state key cannot be empty")
        if not callable(reducer):
            raise TypeError("reducer must be callable")
        self._reducers[key] = reducer
        return self

    def compile(
        self,
        *,
        checkpointer: Checkpointer | None = None,
        max_steps: int = 100,
        max_seconds: float | None = None,
        parallel: bool = True,
    ) -> "CompiledGraph":
        self._validate()
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_seconds is not None and max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        return CompiledGraph(
            nodes=dict(self._nodes),
            edges=list(self._edges),
            routes=list(self._routes),
            reducers=dict(self._reducers),
            checkpointer=checkpointer,
            max_steps=max_steps,
            max_seconds=max_seconds,
            parallel=parallel,
        )

    def to_mermaid(self) -> str:
        """Return a dependency-free Mermaid flowchart of the declared graph."""

        self._validate()
        ids = {name: f"n{i}" for i, name in enumerate(self._nodes)}

        def node_id(name: str) -> str:
            if name == START:
                return "start"
            if name == END:
                return "end"
            return ids[name]

        lines = ["flowchart TD", "    start((Start))", "    end((End))"]
        for name, ident in ids.items():
            lines.append(f'    {ident}["{_mermaid_text(name)}"]')
        for edge in self._edges:
            label = edge.label or (_callable_name(edge.condition) if edge.condition else "")
            arrow = f"-->|{_mermaid_text(label)}|" if label else "-->"
            if len(edge.sources) == 1:
                lines.append(
                    f"    {node_id(edge.sources[0])} {arrow} {node_id(edge.target)}"
                )
            else:
                join_id = edge.key.replace("-", "_")
                lines.append(f"    {join_id}{{{{all}}}}")
                for source in edge.sources:
                    lines.append(f"    {node_id(source)} --> {join_id}")
                lines.append(f"    {join_id} {arrow} {node_id(edge.target)}")
        for i, route in enumerate(self._routes):
            if route.paths:
                for key, target in route.paths.items():
                    label = route.labels.get(key, str(key))
                    lines.append(
                        f"    {node_id(route.source)} -. {_mermaid_text(label)} .-> {node_id(target)}"
                    )
            else:
                lines.append(
                    f'    {node_id(route.source)} -. "{_mermaid_text(_callable_name(route.router))}" .-> route{i}[dynamic route]'
                )
        return "\n".join(lines)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise GraphValidationError("node names must be non-empty strings")
        if name in (START, END):
            raise GraphValidationError(f"{name!r} is reserved")

    def _validate(self) -> None:
        if not self._nodes:
            raise GraphValidationError("graph has no nodes")
        if not any(edge.sources == (START,) for edge in self._edges) and not any(
            route.source == START for route in self._routes
        ):
            raise GraphValidationError("graph has no edge out of START")
        valid_sources = set(self._nodes) | {START}
        valid_targets = set(self._nodes) | {END}
        for edge in self._edges:
            missing = set(edge.sources) - valid_sources
            if missing:
                raise GraphValidationError(f"edge has unknown source(s): {sorted(missing)}")
            if edge.target not in valid_targets:
                raise GraphValidationError(f"edge targets unknown node {edge.target!r}")
        for route in self._routes:
            if route.source not in valid_sources:
                raise GraphValidationError(f"route has unknown source {route.source!r}")
            if route.paths:
                missing = set(route.paths.values()) - valid_targets
                if missing:
                    raise GraphValidationError(f"route targets unknown node(s): {sorted(missing)}")
        for name, node in self._nodes.items():
            if node.on_error is not None and node.on_error not in valid_targets:
                raise GraphValidationError(
                    f"node {name!r} has unknown error target {node.on_error!r}"
                )


class CompiledGraph:
    """Validated, executable graph returned by :meth:`StateGraph.compile`."""

    def __init__(
        self,
        *,
        nodes: dict[str, _Node],
        edges: list[_Edge],
        routes: list[_Route],
        reducers: dict[str, Reducer],
        checkpointer: Checkpointer | None,
        max_steps: int,
        max_seconds: float | None,
        parallel: bool,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._routes = routes
        self._reducers = reducers
        self._checkpointer = checkpointer
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.parallel = parallel

    def invoke(
        self,
        state: Mapping[str, Any] | None = None,
        *,
        thread_id: str | None = None,
        observer: Observer | None = None,
    ) -> GraphResult:
        initial = dict(state or {})
        arrivals: dict[str, list[str]] = {}
        ready, ended = self._transitions([START], initial, arrivals, {})
        if ended:
            return GraphResult("completed", initial, 0, "end", [])
        checkpoint = Checkpoint(initial, ready, arrivals, 0, [])
        return self._run(checkpoint, thread_id=thread_id, observer=observer)

    def resume(
        self,
        thread_id: str,
        update: Mapping[str, Any] | None = None,
        *,
        observer: Observer | None = None,
    ) -> GraphResult:
        if self._checkpointer is None:
            raise GraphError("resume requires a graph compiled with a checkpointer")
        checkpoint = self._checkpointer.load(thread_id)
        if checkpoint is None:
            raise GraphError(f"no checkpoint found for thread {thread_id!r}")
        checkpoint.state = self._merge(checkpoint.state, [dict(update or {})])
        return self._run(checkpoint, thread_id=thread_id, observer=observer)

    def _run(
        self,
        checkpoint: Checkpoint,
        *,
        thread_id: str | None,
        observer: Observer | None,
    ) -> GraphResult:
        state = dict(checkpoint.state)
        ready = list(checkpoint.ready)
        arrivals = {key: list(value) for key, value in checkpoint.arrivals.items()}
        history = list(checkpoint.history)
        step = checkpoint.step
        started = time.monotonic()
        self._emit(observer, "graph_started", step, data={"ready": ready})

        while ready:
            if step >= self.max_steps:
                return self._finish(
                    "failed", state, step, "max_steps", history, observer,
                    error=f"graph exceeded max_steps={self.max_steps}",
                )
            if self.max_seconds is not None and time.monotonic() - started >= self.max_seconds:
                return self._finish(
                    "failed", state, step, "max_seconds", history, observer,
                    error=f"graph exceeded max_seconds={self.max_seconds}",
                )

            active = _unique(ready)
            step += 1
            self._emit(observer, "step_started", step, data={"active": active})
            for name in active:
                self._emit(observer, "node_started", step, node=name)

            snapshot = MappingProxyType(dict(state))
            runs = self._execute(active, snapshot)
            traces = [run.trace for run in runs]
            history.append(StepTrace(step, active, traces))
            for run in runs:
                kind = "node_interrupted" if run.interrupt else (
                    "node_failed" if run.trace.error and run.command is None else "node_finished"
                )
                event_data = run.trace.to_dict()
                if run.command is not None:
                    event_data["update"] = _preview_update(run.command.update)
                if run.interrupt is not None:
                    event_data["prompt"] = run.interrupt.prompt
                    event_data["data"] = run.interrupt.data
                self._emit(observer, kind, step, node=run.trace.name, data=event_data)

            commands: dict[str, Command] = {}
            updates: list[State] = []
            interrupted: list[Interrupt] = []
            interrupted_nodes: list[str] = []
            fatal: list[_NodeRun] = []
            for run in runs:
                if run.interrupt is not None:
                    interrupted.append(run.interrupt)
                    interrupted_nodes.append(run.trace.name)
                    updates.append(run.interrupt.update)
                    continue
                if run.command is not None:
                    commands[run.trace.name] = run.command
                    updates.append(run.command.update)
                    continue
                node = self._nodes[run.trace.name]
                if node.on_error is not None:
                    commands[run.trace.name] = Command(
                        update={
                            "last_error": run.trace.error,
                            "failed_node": run.trace.name,
                        },
                        goto=node.on_error,
                    )
                    updates.append(commands[run.trace.name].update)
                else:
                    fatal.append(run)

            try:
                state = self._merge(state, updates)
            except Exception as exc:  # reducer/conflict failures are graph failures
                return self._finish(
                    "failed", state, step, "state_merge_error", history, observer,
                    error=f"{type(exc).__name__}: {exc}",
                )

            if fatal:
                error = "; ".join(run.trace.error for run in fatal)
                return self._finish(
                    "failed", state, step, "node_error", history, observer, error=error,
                )

            completed = [name for name in active if name not in interrupted_nodes]
            try:
                next_ready, ended = self._transitions(completed, state, arrivals, commands)
            except Exception as exc:
                return self._finish(
                    "failed", state, step, "routing_error", history, observer,
                    error=f"{type(exc).__name__}: {exc}",
                )

            if interrupted:
                # Re-run paused nodes after resume; preserve transitions from nodes
                # that completed beside them in this same superstep.
                ready = _unique([*interrupted_nodes, *next_ready])
                saved = Checkpoint(state, ready, arrivals, step, history)
                if self._checkpointer is not None:
                    if not thread_id:
                        return self._finish(
                            "failed", state, step, "missing_thread_id", history, observer,
                            error="an interrupt requires thread_id when checkpointing is enabled",
                        )
                    self._checkpointer.save(thread_id, saved)
                self._emit(
                    observer,
                    "graph_interrupted",
                    step,
                    data={"prompts": [item.prompt for item in interrupted]},
                )
                return GraphResult(
                    "interrupted", state, step, "interrupt", history, interrupted
                )

            if ended:
                if self._checkpointer is not None and thread_id:
                    self._checkpointer.delete(thread_id)
                return self._finish("completed", state, step, "end", history, observer)

            ready = next_ready
            self._emit(observer, "step_finished", step, data={"ready": ready})

        return self._finish(
            "dead_end", state, step, "no_ready_nodes", history, observer,
            error="execution stopped without reaching END",
        )

    def _execute(self, active: list[str], snapshot: Mapping[str, Any]) -> list[_NodeRun]:
        if self.parallel and len(active) > 1:
            with ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="agentgraph") as pool:
                futures = [pool.submit(self._execute_one, name, snapshot) for name in active]
                return [future.result() for future in futures]
        return [self._execute_one(name, snapshot) for name in active]

    def _execute_one(self, name: str, snapshot: Mapping[str, Any]) -> _NodeRun:
        spec = self._nodes[name]
        started = time.monotonic()
        delay = spec.retry.initial_delay
        attempts = 0
        error = ""
        while attempts < spec.retry.max_attempts:
            attempts += 1
            try:
                value = spec.action(snapshot)
                if isinstance(value, Interrupt):
                    return _NodeRun(
                        NodeTrace(
                            name, attempts, time.monotonic() - started,
                            list(value.update), interrupted=True,
                        ),
                        interrupt=value,
                    )
                if value is None:
                    command = Command()
                elif isinstance(value, Command):
                    command = value
                elif isinstance(value, Mapping):
                    command = Command(update=dict(value))
                else:
                    raise TypeError(
                        "node must return a mapping, Command, Interrupt, or None; "
                        f"got {type(value).__name__}"
                    )
                self._validate_goto(command.goto)
                return _NodeRun(
                    NodeTrace(
                        name, attempts, time.monotonic() - started, list(command.update)
                    ),
                    command=command,
                )
            except Exception as exc:  # node errors are data, never process crashes
                error = f"{type(exc).__name__}: {exc}"
                should_retry = (
                    isinstance(exc, spec.retry.retry_on)
                    and attempts < spec.retry.max_attempts
                )
                if not should_retry:
                    break
                if delay:
                    time.sleep(delay)
                    delay *= spec.retry.backoff
        return _NodeRun(NodeTrace(name, attempts, time.monotonic() - started, error=error))

    def _validate_goto(self, goto: str | list[str] | tuple[str, ...] | None) -> None:
        if goto is None:
            return
        targets = [goto] if isinstance(goto, str) else list(goto)
        unknown = set(targets) - (set(self._nodes) | {END})
        if unknown:
            raise GraphValidationError(f"Command.goto contains unknown node(s): {sorted(unknown)}")

    def _transitions(
        self,
        completed: list[str],
        state: State,
        arrivals: dict[str, list[str]],
        commands: Mapping[str, Command],
    ) -> tuple[list[str], bool]:
        destinations: list[str] = []
        for source in completed:
            command = commands.get(source)
            if command is not None and command.goto is not None:
                selected = [command.goto] if isinstance(command.goto, str) else list(command.goto)
                destinations.extend(selected)
                continue

            for edge in self._edges:
                if source not in edge.sources:
                    continue
                if len(edge.sources) == 1:
                    if edge.condition is None or edge.condition(MappingProxyType(state)):
                        destinations.append(edge.target)
                    continue
                seen = arrivals.setdefault(edge.key, [])
                if source not in seen:
                    seen.append(source)
                if set(seen) == set(edge.sources):
                    arrivals[edge.key] = []
                    if edge.condition is None or edge.condition(MappingProxyType(state)):
                        destinations.append(edge.target)

            for route in self._routes:
                if route.source != source:
                    continue
                choice = route.router(MappingProxyType(state))
                choices = _route_choices(choice)
                for item in choices:
                    if route.paths is None:
                        target = item
                    else:
                        if item not in route.paths:
                            raise GraphError(
                                f"router from {source!r} returned unmapped path {item!r}"
                            )
                        target = route.paths[item]
                    if target not in self._nodes and target != END:
                        raise GraphError(
                            f"router from {source!r} returned unknown target {target!r}"
                        )
                    destinations.append(target)

        ended = END in destinations
        return _unique([name for name in destinations if name != END]), ended

    def _merge(self, state: State, updates: list[State]) -> State:
        merged = dict(state)
        by_key: dict[str, list[Any]] = {}
        for update in updates:
            for key, value in update.items():
                by_key.setdefault(key, []).append(value)
        for key, values in by_key.items():
            reducer = self._reducers.get(key)
            if reducer is not None:
                if key in merged:
                    current = merged[key]
                    pending = values
                else:
                    current = values[0]
                    pending = values[1:]
                for value in pending:
                    current = reducer(current, value)
                merged[key] = current
                continue
            if len(values) > 1 and any(value != values[0] for value in values[1:]):
                raise StateConflictError(
                    f"parallel nodes wrote different values to {key!r}; register a reducer"
                )
            merged[key] = values[-1]
        return merged

    def _finish(
        self,
        status: str,
        state: State,
        step: int,
        reason: str,
        history: list[StepTrace],
        observer: Observer | None,
        *,
        error: str = "",
    ) -> GraphResult:
        kind = "graph_finished" if status == "completed" else "graph_failed"
        self._emit(observer, kind, step, data={"status": status, "reason": reason, "error": error})
        return GraphResult(status, state, step, reason, history, error=error)

    @staticmethod
    def _emit(
        observer: Observer | None,
        kind: str,
        step: int,
        *,
        node: str = "",
        data: Optional[State] = None,
    ) -> None:
        if observer is None:
            return
        try:
            observer(GraphEvent(kind, step, node, data or {}))
        except Exception:
            pass  # observability can never break control flow


def _route_choices(choice: Any) -> list[Any]:
    if choice is None:
        return []
    if isinstance(choice, str):
        return [choice]
    try:
        return list(choice)
    except TypeError:
        return [choice]


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _callable_name(value: Any) -> str:
    return getattr(value, "__name__", value.__class__.__name__) if value else ""


def _mermaid_text(value: Any) -> str:
    return str(value).replace('"', "'").replace("\n", " ")


def _preview_update(update: Mapping[str, Any], limit: int = 1000) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in update.items():
        if isinstance(value, str) and len(value) > limit:
            preview[key] = value[:limit] + f"...[truncated {len(value) - limit} chars]"
        else:
            preview[key] = value
    return preview
