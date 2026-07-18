from __future__ import annotations

import json

import pytest

from agentgraph import AgentNode, extract_json
from agentgraph.adapters import CliAgent


class RecordingAgent:
    def __init__(self, text):
        self.text = text
        self.requests = []

    def run(self, request):
        from agentgraph import AgentResponse

        self.requests.append(request)
        return AgentResponse(self.text)


def test_agent_node_builds_prompt_and_parses_update():
    agent = RecordingAgent('{"answer": 42}')
    node = AgentNode(
        agent,
        role="answerer",
        system="system",
        prompt=lambda state: f"Question: {state['question']}",
        parser=extract_json,
        expects_json=True,
    )

    update = node({"question": "life"})

    assert update == {"answer": 42}
    assert agent.requests[0].prompt == "Question: life"
    assert agent.requests[0].expects_json is True


def test_extract_json_accepts_prose_and_fences():
    assert extract_json('Here: {"a": 1} done') == {"a": 1}
    assert extract_json("```json\n[1, 2]\n```") == [1, 2]
    with pytest.raises(ValueError, match="no parseable JSON"):
        extract_json("not json")


def test_cli_agent_uses_stdin_when_template_has_no_placeholder(tmp_path):
    script = "import sys, json; print(json.dumps({'received': sys.stdin.read()}))"
    agent = CliAgent(["python3", "-c", script])
    from agentgraph import AgentRequest

    response = agent.run(AgentRequest("role", "SYS", "PROMPT"))

    assert json.loads(response.text)["received"] == "SYS\n\nPROMPT"


def test_cli_agent_nonzero_exit_becomes_an_error():
    agent = CliAgent(["python3", "-c", "import sys; print('bad'); sys.exit(7)"])
    from agentgraph import AgentRequest

    with pytest.raises(RuntimeError, match="exited 7"):
        agent.run(AgentRequest("role", "", "prompt"))
