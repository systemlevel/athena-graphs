# Declarative graph specification

Pass a JSON object with `nodes`, `edges`, and an optional `max_steps` to `graph_start` or `graph_validate`.

```json
{
  "name": "research-build-review",
  "max_steps": 12,
  "nodes": [
    {
      "id": "research",
      "kind": "agent",
      "role": "researcher",
      "system": "Investigate the goal without editing files.",
      "prompt": "GOAL:\n$goal",
      "output_key": "research"
    },
    {
      "id": "build",
      "kind": "agent",
      "role": "worker",
      "system": "Implement and verify the goal.",
      "prompt": "GOAL:\n$goal\n\nRESEARCH:\n$research",
      "output_key": "work_output"
    },
    {
      "id": "review",
      "kind": "agent",
      "role": "reviewer",
      "system": "Return only JSON with approved, issues, and summary.",
      "prompt": "CRITERIA:\n$success_criteria\n\nWORK:\n$work_output",
      "response": "json",
      "merge": true,
      "retry": {"max_attempts": 2}
    }
  ],
  "edges": [
    {"from": "__start__", "to": "research"},
    {"from": "research", "to": "build"},
    {"from": "build", "to": "review"},
    {"from": "review", "to": "__end__", "when": {"key": "approved", "equals": true}},
    {"from": "review", "to": "build", "when": {"key": "approved", "equals": false}}
  ]
}
```

## Nodes

- `agent`: call the selected backend. Use `$state_key` placeholders in `prompt`. Set `response` to `text` or `json`; set `merge: true` to merge a JSON object into state.
- `human`: interrupt until `answer_key` exists. Provide `question` and optional `data_keys` to show relevant state.
- `set`: write the literal/template values in `update` without calling a model.

Agent nodes accept `role`, `system`, `prompt`, `output_key`, `response`, `merge`, `retry`, and `on_error`. Retry supports `max_attempts`, `initial_delay`, and `backoff`.

## Edges

Use `from` as a node id, `__start__`, or an array of node ids for an all-source join. Use `to` as a node id or `__end__`.

`when` supports:

- `{"key": "approved", "equals": true}`
- `not_equals`, `exists`, `truthy`, `falsy`, or `contains`
- nested `{"all": [...]}`, `{"any": [...]}`, and `{"not": {...}}`

Use dotted keys such as `review.approved` for nested state.

## Reducers

Parallel nodes cannot write different values to the same key without an explicit reducer. Declare `reducers` as an object mapping state keys to `append`, `sum`, or `merge`. Prefer unique output keys when possible.
