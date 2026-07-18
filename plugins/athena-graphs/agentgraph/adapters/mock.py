"""Deterministic backend that exercises branching and revision in demos/tests."""

from __future__ import annotations

import json
from typing import Callable

from ..agent import AgentRequest, AgentResponse


class MockAgent:
    def __init__(
        self,
        *,
        tasks: list[str] | None = None,
        accept_on_review: int = 2,
        worker_fn: Callable[[AgentRequest], str] | None = None,
    ) -> None:
        self.tasks = tasks or [
            "Research the relevant facts",
            "Produce the requested deliverable",
            "Check the result against the success criteria",
        ]
        self.accept_on_review = accept_on_review
        self.worker_fn = worker_fn
        self.review_calls = 0

    def run(self, request: AgentRequest) -> AgentResponse:
        if request.role == "planner":
            return AgentResponse(
                json.dumps(
                    [{"id": f"task-{i + 1}", "description": task} for i, task in enumerate(self.tasks)]
                )
            )
        if request.role == "worker":
            if self.worker_fn:
                return AgentResponse(self.worker_fn(request))
            task_id = request.context.get("task_id", "task")
            return AgentResponse(f"Completed {task_id}: concrete deliverable produced.")
        if request.role == "reviewer":
            self.review_calls += 1
            approved = self.review_calls >= self.accept_on_review
            return AgentResponse(
                json.dumps(
                    {
                        "approved": approved,
                        "issues": [] if approved else ["Add one more concrete revision pass."],
                        "summary": "All criteria met." if approved else "A revision is required.",
                    }
                )
            )
        if request.role == "reviser":
            return AgentResponse(
                json.dumps(
                    [
                        {
                            "id": "revision-1",
                            "description": "Resolve every reviewer issue and preserve completed work",
                        }
                    ]
                )
            )
        if request.expects_json:
            return AgentResponse(
                json.dumps({"approved": True, "issues": [], "summary": "Mock review passed."})
            )
        return AgentResponse(f"Completed {request.role}: mock deliverable produced.")
