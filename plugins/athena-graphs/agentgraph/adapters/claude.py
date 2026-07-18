"""Anthropic Claude API backend (optional dependency)."""

from __future__ import annotations

import os

from ..agent import AgentRequest, AgentResponse


class ClaudeAgent:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        *,
        api_key: str | None = None,
        max_tokens: int = 4096,
        client=None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("ClaudeAgent requires: pip install 'athena-graphs[claude]'") from exc
        self.model = model
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def run(self, request: AgentRequest) -> AgentResponse:
        system = request.system
        if request.expects_json:
            system += "\n\nReturn ONLY the JSON value, with no prose or code fence."
        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": request.prompt}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return AgentResponse(text, raw=message)
