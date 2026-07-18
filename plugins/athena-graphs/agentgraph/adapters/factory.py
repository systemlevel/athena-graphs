"""Backend discovery shared by the CLI and MCP server."""

from __future__ import annotations

import os
from typing import Any

from .claude import ClaudeAgent
from .cli import CliAgent
from .grok import GrokAgent
from .mock import MockAgent


BACKENDS = ("mock", "claude", "grok_api", "claude_code", "codex", "opencode", "aider", "grok_build")

_ALIASES = {
    "grok": "grok_build",
    "grok-build": "grok_build",
    "grok-api": "grok_api",
    "xai": "grok_api",
    "claude-code": "claude_code",
}


def resolve_backend(name: str, *, caller_agent: str | None = None) -> str:
    normalized = _ALIASES.get(name.lower(), name.lower())
    if normalized == "auto":
        hint = (caller_agent or _environment_hint() or "").lower()
        if "codex" in hint:
            normalized = "codex"
        elif "opencode" in hint:
            normalized = "opencode"
        elif "grok" in hint:
            normalized = "grok_build"
        else:
            normalized = "claude_code"
    if normalized not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; choose one of: {', '.join(BACKENDS)}")
    return normalized


def build_agent(
    backend: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
    skip_permissions: bool = False,
    caller_agent: str | None = None,
) -> Any:
    name = resolve_backend(backend, caller_agent=caller_agent)
    common = {"cwd": cwd, "timeout": timeout}
    if name == "mock":
        return MockAgent()
    if name == "claude":
        return ClaudeAgent(**({"model": model} if model else {}))
    if name == "grok_api":
        options = {"model": model} if model else {}
        if timeout is not None:
            options["timeout"] = timeout
        return GrokAgent(**options)
    factory = {
        "claude_code": CliAgent.claude_code,
        "codex": CliAgent.codex,
        "opencode": CliAgent.opencode,
        "aider": CliAgent.aider,
        "grok_build": CliAgent.grok_build,
    }[name]
    options: dict[str, Any] = {**common, "skip_permissions": skip_permissions}
    if name in ("claude_code", "grok_build") and model:
        options["model"] = model
    return factory(**options)


def _environment_hint() -> str | None:
    if os.environ.get("OPENCODE") or os.environ.get("OPENCODE_RUN_ID"):
        return "opencode"
    if os.environ.get("CODEX_RUN_ID") or os.environ.get("CODEX_SESSION_ID"):
        return "codex"
    if os.environ.get("GROK_AGENT"):
        return "grok"
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE"):
        return "claude"
    return None
