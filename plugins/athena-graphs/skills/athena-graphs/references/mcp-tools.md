# MCP tool contracts

- `graph_start`: validate and start a detached run. Returns `run_id`, Mermaid diagram, and effective parallel mode.
- `graph_status`: return current node/phase, step, running state, error, and pending interrupts.
- `graph_tail`: return events after `cursor`. Pass the returned cursor to the next call.
- `graph_resume`: merge an answer object into the checkpointed state and continue in the background.
- `graph_result`: return the structured final result when ready. Prefer polling status over a long blocking wait.
- `graph_diagram`: render a proposed topology or saved run as Mermaid.
- `graph_validate`: validate topology structure and render it without starting agents.
- `graph_list`: list recent durable runs.
- `list_backends`: list built-in API and coding-agent backends.
- `doctor`: check target-directory access and backend availability.

## Status handling

- `running`: continue polling status and tailing events.
- `interrupted`: read `interrupts`, ask the user, then call `graph_resume` with the expected answer key.
- `completed`: read `graph_result` and verify the actual deliverable.
- `dead_end`: the topology stopped without reaching `__end__`; inspect routing and state.
- `failed`: inspect `error` and recent events before deciding whether a corrected topology or user input is needed.
- `orphaned`: the MCP process stopped mid-run. Completed/interrupted runs survive restarts, but in-flight nodes cannot be reconstructed safely.

Runs and checkpoints live under `ATHENA_GRAPHS_HOME` when configured, otherwise under `~/.athena-graphs`.
