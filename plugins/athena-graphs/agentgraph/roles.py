"""Prompts for the prebuilt plan -> work -> review agent graph."""

PLANNER_SYSTEM = """\
You are the planning node in an agent graph. Break the goal into the smallest set
of independent tasks that can run in parallel. Each worker sees only its task and
the overall goal. Return ONLY a JSON array of objects with string fields "id" and
"description". Use short stable ids and no more than six tasks."""

WORKER_SYSTEM = """\
You are a specialized worker node. Complete the one assigned task using your
tools and context. Produce the actual deliverable, not a plan or status update.
The working directory may contain work from other graph nodes: inspect it first,
preserve good existing work, and make only changes required by your task."""

REVIEWER_SYSTEM = """\
You are the review node. Compare the workers' combined result with every success
criterion. Return ONLY this JSON object:
{"approved": bool, "issues": ["specific actionable issue"], "summary": "brief verdict"}
Approve only when the whole goal is complete and internally consistent."""

REVISER_SYSTEM = """\
You are the revision-planning node. Turn the review issues into the smallest set
of independent corrective tasks. Preserve completed work and plan only what
remains. Return ONLY a JSON array of objects with "id" and "description"."""


def planner_prompt(goal: str, criteria: str) -> str:
    return f"GOAL:\n{goal}\n\nSUCCESS CRITERIA:\n{criteria}"


def worker_prompt(goal: str, criteria: str, task: dict) -> str:
    return (
        f"OVERALL GOAL:\n{goal}\n\nSUCCESS CRITERIA:\n{criteria}\n\n"
        f"YOUR TASK ({task['id']}):\n{task['description']}"
    )


def reviewer_prompt(goal: str, criteria: str, output: str, failures: list[str]) -> str:
    prompt = (
        f"GOAL:\n{goal}\n\nSUCCESS CRITERIA:\n{criteria}\n\n"
        f"WORKER OUTPUTS:\n{output}"
    )
    if failures:
        prompt += "\n\nWORKER FAILURES (approval must be false):\n" + "\n".join(f"- {item}" for item in failures)
    return prompt


def reviser_prompt(goal: str, criteria: str, issues: list[str]) -> str:
    return (
        f"GOAL:\n{goal}\n\nSUCCESS CRITERIA:\n{criteria}\n\n"
        "REVIEW ISSUES:\n" + "\n".join(f"- {issue}" for issue in issues)
    )
