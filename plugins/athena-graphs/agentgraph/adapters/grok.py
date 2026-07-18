"""xAI Grok API backend through the OpenAI-compatible client."""

from __future__ import annotations

import os

from ..agent import AgentRequest, AgentResponse


class GrokAgent:
    def __init__(
        self,
        model: str = "grok-build-0.1",
        *,
        api_key: str | None = None,
        base_url: str = "https://api.x.ai/v1",
        timeout: float | None = None,
        client=None,
    ) -> None:
        self.model = model
        if client is not None:
            self.client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("GrokAgent requires: pip install 'athena-graphs[grok]'") from exc
        options: dict[str, object] = {
            "api_key": api_key or os.environ.get("XAI_API_KEY"),
            "base_url": base_url,
        }
        if timeout is not None:
            options["timeout"] = timeout
        self.client = OpenAI(**options)

    def run(self, request: AgentRequest) -> AgentResponse:
        system = request.system
        if request.expects_json:
            system += "\n\nReturn ONLY the JSON value, with no prose or code fence."
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": request.prompt},
            ],
        )
        text = _response_text(response)
        return AgentResponse(text=text, raw=response)


def _response_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text is not None:
        return str(text)
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", None) in ("output_text", "text"):
                value = getattr(part, "text", None)
                if value is not None:
                    chunks.append(str(value))
    return "".join(chunks)
