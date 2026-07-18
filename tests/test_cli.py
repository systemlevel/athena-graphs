from __future__ import annotations

import json

from agentgraph.cli import main


def test_cli_mock_run(capsys):
    code = main(["run", "--goal", "g", "--criteria", "c", "--backend", "mock", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["approved"] is True


def test_cli_diagram(capsys):
    assert main(["diagram"]) == 0
    assert "flowchart TD" in capsys.readouterr().out


def test_cli_rejects_unapproved_work(capsys):
    code = main([
        "run", "--goal", "g", "--backend", "mock", "--max-revisions", "0", "--json"
    ])
    output = json.loads(capsys.readouterr().out)

    assert code == 1
    assert output["approved"] is False
