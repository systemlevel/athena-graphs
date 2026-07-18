"""Durable detached graph runs for the MCP surface."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .adapters import build_agent, resolve_backend
from .checkpoint import JsonFileCheckpointer
from .spec import compile_topology, normalize_topology, topology_mermaid
from .types import GraphEvent


class GraphRunManager:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        configured = root or os.environ.get("ATHENA_GRAPHS_HOME")
        self.root = Path(configured or (Path.home() / ".athena-graphs")) / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    def start(
        self,
        *,
        goal: str,
        success_criteria: str,
        topology: Mapping[str, Any] | None,
        backend: str,
        caller_agent: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        skip_permissions: bool = False,
        max_steps: int | None = None,
        max_seconds: float | None = None,
        parallel: bool | None = None,
    ) -> dict[str, Any]:
        if not goal.strip():
            raise ValueError("goal cannot be empty")
        normalized = normalize_topology(topology)
        # Validate topology synchronously so callers never receive a run id for a
        # malformed graph. The backend is not called during compilation.
        topology_mermaid(normalized)
        resolved = resolve_backend(backend, caller_agent=caller_agent)
        effective_parallel = (
            parallel
            if parallel is not None
            else not (cwd is not None and resolved in {"claude_code", "codex", "opencode", "aider", "grok_build"})
        )
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True)
        request = {
            "run_id": run_id,
            "goal": goal,
            "success_criteria": success_criteria,
            "topology": normalized,
            "backend": resolved,
            "caller_agent": caller_agent,
            "cwd": os.path.abspath(cwd) if cwd else None,
            "model": model,
            "timeout": timeout,
            "skip_permissions": skip_permissions,
            "max_steps": max_steps,
            "max_seconds": max_seconds,
            "parallel": effective_parallel,
        }
        _write_json(run_dir / "request.json", request)
        self._write_status(
            run_id,
            status="starting",
            running=True,
            step=0,
            phase="starting",
            error="",
        )
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, False, None),
            name=f"athena-graph-{run_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return {
            "status": "running",
            "run_id": run_id,
            "diagram": topology_mermaid(normalized),
            "parallel": effective_parallel,
            "status_hint": "Call graph_status, then graph_tail while running; call graph_result when finished.",
        }

    def resume(self, run_id: str, answers: Mapping[str, Any]) -> dict[str, Any]:
        status = self.status(run_id)
        if status.get("running"):
            raise RuntimeError(f"run {run_id!r} is still running")
        if status.get("status") != "interrupted":
            raise RuntimeError(f"run {run_id!r} is not waiting for input")
        if not isinstance(answers, Mapping) or not answers:
            raise ValueError("answers must be a non-empty object")
        self._write_status(
            run_id,
            status="resuming",
            running=True,
            step=int(status.get("step", 0)),
            phase="resuming",
            error="",
        )
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, True, dict(answers)),
            name=f"athena-graph-{run_id}-resume",
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return {"status": "running", "run_id": run_id, "resumed": True}

    def status(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "status.json"
        if not path.exists():
            raise KeyError(f"unknown run_id {run_id!r}")
        value = _read_json(path)
        owner_pid = value.get("owner_pid")
        thread = self._threads.get(run_id)
        if value.get("running") and owner_pid != os.getpid() and not (thread and thread.is_alive()):
            value.update(
                {
                    "status": "orphaned",
                    "running": False,
                    "phase": "orphaned",
                    "error": "the MCP process stopped before this run finished",
                    "updated_at": time.time(),
                }
            )
            _write_json(path, value)
        return value

    def tail(self, run_id: str, *, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        if cursor < 0:
            raise ValueError("cursor cannot be negative")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        path = self._run_dir(run_id) / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        selected = lines[cursor : cursor + limit]
        events = [json.loads(line) for line in selected]
        next_cursor = cursor + len(events)
        return {
            "run_id": run_id,
            "events": events,
            "cursor": next_cursor,
            "more": next_cursor < len(lines),
            "running": bool(self.status(run_id).get("running")),
        }

    def result(self, run_id: str, *, wait: bool = False, timeout: float = 0.0) -> dict[str, Any]:
        if wait:
            deadline = time.monotonic() + max(0.0, timeout)
            while self.status(run_id).get("running") and time.monotonic() < deadline:
                time.sleep(0.05)
        status = self.status(run_id)
        path = self._run_dir(run_id) / "result.json"
        if path.exists():
            return _read_json(path)
        return {
            "status": status.get("status"),
            "running": status.get("running"),
            "run_id": run_id,
            "message": "result is not ready",
        }

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        directories = sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        return [self.status(path.name) for path in directories[:limit]]

    def diagram(self, run_id: str) -> str:
        request = _read_json(self._run_dir(run_id) / "request.json")
        return topology_mermaid(request["topology"])

    def _execute(
        self,
        run_id: str,
        resume: bool,
        answers: Mapping[str, Any] | None,
    ) -> None:
        run_dir = self._run_dir(run_id)
        try:
            request = _read_json(run_dir / "request.json")
            agent = build_agent(
                request["backend"],
                caller_agent=request.get("caller_agent"),
                cwd=request.get("cwd"),
                model=request.get("model"),
                timeout=request.get("timeout"),
                skip_permissions=bool(request.get("skip_permissions")),
            )
            app = compile_topology(
                request["topology"],
                agent,
                checkpointer=JsonFileCheckpointer(run_dir / "checkpoints"),
                max_steps=request.get("max_steps"),
                max_seconds=request.get("max_seconds"),
                parallel=bool(request.get("parallel", True)),
            )

            def observe(event: GraphEvent) -> None:
                self._append_event(run_id, event.to_dict())
                current = self.status(run_id)
                self._write_status(
                    run_id,
                    status="running",
                    running=True,
                    step=event.step,
                    phase=event.node or event.kind,
                    error="",
                    started_at=current.get("started_at"),
                )

            if resume:
                result = app.resume(run_id, answers or {}, observer=observe)
            else:
                result = app.invoke(
                    {
                        "goal": request["goal"],
                        "success_criteria": request["success_criteria"],
                    },
                    thread_id=run_id,
                    observer=observe,
                )
            payload = result.to_dict()
            payload.update(
                {
                    "run_id": run_id,
                    "running": False,
                    "diagram": topology_mermaid(request["topology"]),
                }
            )
            _write_json(run_dir / "result.json", payload)
            self._write_status(
                run_id,
                status=result.status,
                running=False,
                step=result.steps,
                phase=result.status,
                error=result.error,
                interrupts=[
                    {"prompt": item.prompt, "data": item.data}
                    for item in result.interrupts
                ],
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._append_event(
                run_id,
                {"kind": "run_error", "step": 0, "node": "", "data": {"error": error}},
            )
            _write_json(
                run_dir / "result.json",
                {
                    "run_id": run_id,
                    "status": "failed",
                    "running": False,
                    "completed": False,
                    "error": error,
                },
            )
            self._write_status(
                run_id,
                status="failed",
                running=False,
                step=0,
                phase="failed",
                error=error,
            )
        finally:
            with self._lock:
                self._threads.pop(run_id, None)

    def _append_event(self, run_id: str, event: Mapping[str, Any]) -> None:
        path = self._run_dir(run_id) / "events.jsonl"
        with self._lock:
            sequence = 0
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    sequence = sum(1 for _ in handle)
            value = {"sequence": sequence, **_jsonable(dict(event))}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")

    def _write_status(
        self,
        run_id: str,
        *,
        status: str,
        running: bool,
        step: int,
        phase: str,
        error: str,
        started_at: float | None = None,
        interrupts: list[dict[str, Any]] | None = None,
    ) -> None:
        now = time.time()
        path = self._run_dir(run_id) / "status.json"
        if started_at is None and path.exists():
            try:
                started_at = float(_read_json(path).get("started_at", now))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                started_at = now
        _write_json(
            path,
            {
                "run_id": run_id,
                "status": status,
                "running": running,
                "step": step,
                "phase": phase,
                "error": error,
                "interrupts": interrupts or [],
                "started_at": started_at if started_at is not None else now,
                "updated_at": now,
                "owner_pid": os.getpid(),
            },
        )

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in run_id):
            raise ValueError("invalid run_id")
        return self.root / run_id


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _jsonable(value)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(item) for item in value]
        return repr(value)
