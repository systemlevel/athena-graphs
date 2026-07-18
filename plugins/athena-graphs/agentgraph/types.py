"""Public data types for the Athena Graphs runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional


START = "__start__"
END = "__end__"

State = dict[str, Any]
StateView = Mapping[str, Any]
NodeAction = Callable[[StateView], Any]
Condition = Callable[[StateView], bool]
Router = Callable[[StateView], Any]
Reducer = Callable[[Any, Any], Any]
Observer = Callable[["GraphEvent"], None]


@dataclass(frozen=True)
class RetryPolicy:
    """Retry a node locally before the graph takes its error path."""

    max_attempts: int = 1
    initial_delay: float = 0.0
    backoff: float = 2.0
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay < 0:
            raise ValueError("initial_delay cannot be negative")
        if self.backoff < 1:
            raise ValueError("backoff must be at least 1")


@dataclass
class Command:
    """A node result that updates state and optionally chooses the next node(s).

    ``goto`` overrides the node's declared outgoing edges for this execution.
    Most nodes can simply return a mapping and use edges declared on the graph.
    """

    update: State = field(default_factory=dict)
    goto: str | list[str] | tuple[str, ...] | None = None


@dataclass
class Interrupt:
    """Pause before completing a node and ask the caller for input.

    The interrupted node is run again after ``resume`` merges the human's answer
    into state. This makes human nodes naturally idempotent: check for the answer,
    interrupt when absent, and continue when present.
    """

    prompt: str
    data: State = field(default_factory=dict)
    update: State = field(default_factory=dict)


def interrupt(prompt: str, *, data: Optional[State] = None, update: Optional[State] = None) -> Interrupt:
    return Interrupt(prompt=prompt, data=data or {}, update=update or {})


@dataclass
class NodeTrace:
    name: str
    attempts: int
    duration: float
    update_keys: list[str] = field(default_factory=list)
    error: str = ""
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "attempts": self.attempts,
            "duration": self.duration,
            "update_keys": self.update_keys,
            "error": self.error,
            "interrupted": self.interrupted,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NodeTrace":
        return cls(
            name=str(value["name"]),
            attempts=int(value["attempts"]),
            duration=float(value["duration"]),
            update_keys=list(value.get("update_keys", [])),
            error=str(value.get("error", "")),
            interrupted=bool(value.get("interrupted", False)),
        )


@dataclass
class StepTrace:
    step: int
    active: list[str]
    nodes: list[NodeTrace]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "active": self.active,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StepTrace":
        return cls(
            step=int(value["step"]),
            active=list(value.get("active", [])),
            nodes=[NodeTrace.from_dict(node) for node in value.get("nodes", [])],
        )


@dataclass
class GraphEvent:
    kind: str
    step: int
    node: str = ""
    data: State = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step": self.step,
            "node": self.node,
            "data": self.data,
            "created_at": self.created_at,
        }


@dataclass
class GraphResult:
    status: str
    state: State
    steps: int
    stop_reason: str
    history: list[StepTrace] = field(default_factory=list)
    interrupts: list[Interrupt] = field(default_factory=list)
    error: str = ""

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "state": self.state,
            "steps": self.steps,
            "stop_reason": self.stop_reason,
            "history": [step.to_dict() for step in self.history],
            "interrupts": [
                {"prompt": item.prompt, "data": item.data, "update": item.update}
                for item in self.interrupts
            ],
            "error": self.error,
        }


@dataclass
class Checkpoint:
    """Serializable continuation state stored between an interrupt and resume."""

    state: State
    ready: list[str]
    arrivals: dict[str, list[str]]
    step: int
    history: list[StepTrace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ready": self.ready,
            "arrivals": self.arrivals,
            "step": self.step,
            "history": [item.to_dict() for item in self.history],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Checkpoint":
        return cls(
            state=dict(value.get("state", {})),
            ready=list(value.get("ready", [])),
            arrivals={str(k): list(v) for k, v in value.get("arrivals", {}).items()},
            step=int(value.get("step", 0)),
            history=[StepTrace.from_dict(item) for item in value.get("history", [])],
        )


class GraphError(RuntimeError):
    pass


class GraphValidationError(GraphError):
    pass


class StateConflictError(GraphError):
    pass


class NodeExecutionError(GraphError):
    def __init__(self, node: str, message: str):
        super().__init__(f"node {node!r} failed: {message}")
        self.node = node
