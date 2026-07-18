---
name: athena-graphs
description: Orchestrate complex work as explicit agent graphs using the Athena Graphs MCP tools. Use when the user explicitly asks for Athena Graphs, asks to graph/orchestrate a task, wants parallel specialized agents, needs branching or review/retry paths, requests human approval checkpoints, or has a multi-stage task whose control flow should be inspectable and durable.
---

# Athena Graphs

Use the Athena Graphs MCP server as the deterministic execution layer. Keep graph mechanics out of the user's way: accept a natural-language goal, infer success criteria when safe, and expose only decisions or progress that help the user.

## Run a graph

1. Inspect the target workspace enough to understand constraints and existing work.
2. Decide whether the default topology is sufficient. Build a custom topology for independent branches, specialized roles, human approval, or task-specific review loops. Read [references/graph-spec.md](references/graph-spec.md) before constructing one.
3. Call `graph_validate` when using a custom topology. Show the Mermaid diagram only when the user asks or when topology approval would materially change the work.
4. Call `graph_start` with the goal, inferred success criteria, target `cwd`, and caller family (`codex`, `claude`, `opencode`, or `grok`). Let backend selection remain `auto` unless the user names one.
5. Treat the returned run as detached. Poll `graph_status`; interleave `graph_tail` calls with concise progress updates. Reuse the returned cursor.
6. When status becomes `interrupted`, ask the pending question and pass the answer object to `graph_resume`.
7. When running becomes false, call `graph_result`. Inspect the resulting files or repository and run proportionate verification before reporting success.

Do not ask the user to install Python packages, construct CLI flags, provide success criteria that can be inferred, or manually poll a run.

## Design the topology

- Give each node one clear responsibility and a unique output key.
- Fan out only independent work. Use a list of sources for an all-source join.
- Keep repository-editing nodes sequential unless they operate in isolated worktrees. The runtime disables parallel execution automatically for built-in coding-agent backends sharing one `cwd`.
- Make reviewers return JSON merged into state, normally `approved`, `issues`, and `summary`.
- Route approval to `__end__`; route rejection to a corrective node and back to review.
- Add a human node only for a consequential choice or when the user requests a checkpoint.
- Set a finite `max_steps` for every topology containing a cycle.
- Prefer the smallest graph that makes the real control flow visible. Do not turn a simple one-step task into ceremony unless the user explicitly requested Athena Graphs.

## Monitor responsibly

Start the run, then stay with it. Use `graph_tail` to report node starts, completions, retries, failures, and output previews. Do not block on `graph_result(wait=true)` for a long run. Do not declare completion solely because the graph reached `__end__`; confirm the final state and deterministic verification evidence.

Read [references/mcp-tools.md](references/mcp-tools.md) for tool contracts and recovery behavior.
