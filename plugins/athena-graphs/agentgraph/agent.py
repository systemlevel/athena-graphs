"""Backend-neutral agent seam and a graph-node adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


@dataclass
class AgentRequest:
    role: str
    system: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    expects_json: bool = False


@dataclass
class AgentResponse:
    text: str
    raw: Any = None


class Agent(Protocol):
    def run(self, request: AgentRequest) -> AgentResponse: ...


class AgentNode:
    """Turn any :class:`Agent` call into a reusable graph node.

    ``prompt`` may be static or derived from state. A parser can return a full
    update mapping; otherwise the response is written under ``output_key``.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        role: str,
        system: str,
        prompt: str | Callable[[Mapping[str, Any]], str],
        output_key: str | None = None,
        parser: Callable[[str], Any] | None = None,
        expects_json: bool = False,
        context: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.agent = agent
        self.role = role
        self.system = system
        self.prompt = prompt
        self.output_key = output_key
        self.parser = parser
        self.expects_json = expects_json
        self.context = context

    def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        prompt = self.prompt(state) if callable(self.prompt) else self.prompt
        response = self.agent.run(
            AgentRequest(
                role=self.role,
                system=self.system,
                prompt=prompt,
                context=self.context(state) if self.context else dict(state),
                expects_json=self.expects_json,
            )
        )
        value = self.parser(response.text) if self.parser else response.text
        if self.output_key is not None:
            return {self.output_key: value}
        if isinstance(value, Mapping):
            return dict(value)
        return {self.role: value}


def extract_json(text: str) -> Any:
    """Recover the first JSON value from plain text or a Markdown fence."""

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no parseable JSON found in response: {stripped[:200]!r}")
