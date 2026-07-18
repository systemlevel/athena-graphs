"""Checkpoint an interrupt and resume the same graph later."""

from __future__ import annotations

from agentgraph import END, START, MemoryCheckpointer, StateGraph, interrupt


def review(state):
    if "approved" not in state:
        return interrupt("Approve this draft?", data={"draft": state["draft"]})
    return {"status": "approved" if state["approved"] else "rejected"}


store = MemoryCheckpointer()
graph = StateGraph()
graph.add_node("draft", lambda _state: {"draft": "Graphs make control flow explicit."})
graph.add_node("review", review)
graph.add_edge(START, "draft")
graph.add_edge("draft", "review")
graph.add_edge("review", END)
app = graph.compile(checkpointer=store)

paused = app.invoke({}, thread_id="demo")
print(paused.status, paused.interrupts[0].prompt)
finished = app.resume("demo", {"approved": True})
print(finished.status, finished.state["status"])
