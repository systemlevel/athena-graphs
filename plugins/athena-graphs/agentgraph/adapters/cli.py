"""Run Claude Code, Codex, OpenCode, Aider, or Grok Build as graph nodes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Callable, Optional, Sequence

from ..agent import AgentRequest, AgentResponse


def _parse_claude_json(output: str) -> str:
    data = json.loads(output)
    if data.get("is_error"):
        raise RuntimeError(f"Claude Code reported an error: {data.get('result', '')}")
    return str(data["result"])


class CliAgent:
    """Backend adapter for a headless command-line coding agent.

    Templates may contain ``{prompt}``, ``{system}``, or ``{combined}``. When no
    prompt placeholder is present, the combined prompt is sent on stdin.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        parse_output: Callable[[str], str] | None = None,
        timeout: float | None = None,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = list(command)
        self.parse_output = parse_output
        self.timeout = timeout
        self.cwd = cwd
        self.extra_env = extra_env
        self._use_stdin = not any(
            marker in argument
            for argument in self.command
            for marker in ("{prompt}", "{combined}")
        )

    def run(self, request: AgentRequest) -> AgentResponse:
        combined = f"{request.system}\n\n{request.prompt}" if request.system else request.prompt
        replacements = {
            "{prompt}": request.prompt,
            "{system}": request.system,
            "{combined}": combined,
        }
        argv = [self._replace(argument, replacements) for argument in self.command]
        resolved = shutil.which(argv[0])
        if resolved:
            argv[0] = resolved
        run_options: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "timeout": self.timeout,
            "cwd": self.cwd,
            "env": {**os.environ, **self.extra_env} if self.extra_env else None,
        }
        if self._use_stdin:
            run_options["input"] = combined
        else:
            run_options["stdin"] = subprocess.DEVNULL
        try:
            process = subprocess.run(argv, **run_options)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"CLI agent timed out after {self.timeout}s: {argv[0]}") from exc
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "").strip()[:1000]
            raise RuntimeError(f"CLI agent exited {process.returncode}: {detail}")
        raw = process.stdout.strip()
        text = self.parse_output(raw) if self.parse_output else raw
        return AgentResponse(text=text, raw=process)

    @staticmethod
    def _replace(argument: str, replacements: dict[str, str]) -> str:
        for key, value in replacements.items():
            argument = argument.replace(key, value)
        return argument

    @classmethod
    def claude_code(
        cls, *, model: str | None = None, skip_permissions: bool = False, **kwargs
    ) -> "CliAgent":
        command = [
            "claude", "-p", "{prompt}", "--append-system-prompt", "{system}",
            "--output-format", "json",
        ]
        if model:
            command += ["--model", model]
        if skip_permissions:
            command.append("--dangerously-skip-permissions")
        return cls(command, parse_output=_parse_claude_json, **kwargs)

    @classmethod
    def codex(cls, *, skip_permissions: bool = False, **kwargs) -> "CliAgent":
        command = ["codex", "exec"]
        if skip_permissions:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        command.append("{combined}")
        return cls(command, **kwargs)

    @classmethod
    def opencode(cls, *, skip_permissions: bool = False, **kwargs) -> "CliAgent":
        command = ["opencode", "run", "--print-logs"]
        if skip_permissions:
            command.append("--dangerously-skip-permissions")
        command.append("{combined}")
        return cls(command, **kwargs)

    @classmethod
    def aider(cls, *, skip_permissions: bool = False, **kwargs) -> "CliAgent":
        command = ["aider", "--message", "{combined}", "--no-auto-commits"]
        command.append("--yes-always" if skip_permissions else "--yes")
        return cls(command, **kwargs)

    @classmethod
    def grok_build(
        cls, *, model: str | None = None, skip_permissions: bool = False, **kwargs
    ) -> "CliAgent":
        command = [
            "grok", "--no-auto-update", "-p", "{combined}",
            "--output-format", "plain", "--no-alt-screen",
        ]
        if skip_permissions:
            command.append("--always-approve")
        if model:
            command += ["-m", model]
        return cls(command, **kwargs)
