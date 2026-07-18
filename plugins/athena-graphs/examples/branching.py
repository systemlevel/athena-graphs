"""Static branching + parallel fan-out + all-source join."""

from __future__ import annotations

import operator
import time

from agentgraph import END, START, StateGraph


def main() -> None:
    graph = StateGraph()
    graph.set_reducer("evidence", operator.add)
    graph.add_node("plan", lambda state: {"topic": state["topic"]})

    def search(_state):
        time.sleep(0.05)
        return {"evidence": ["web result"]}

    def retrieve(_state):
        time.sleep(0.05)
        return {"evidence": ["vector result"]}

    graph.add_node("search", search)
    graph.add_node("retrieve", retrieve)
    graph.add_node("analyze", lambda state: {"answer": " + ".join(state["evidence"])})
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_edge("plan", "retrieve")
    graph.add_edge(["search", "retrieve"], "analyze")
    graph.add_edge("analyze", END)

    result = graph.compile().invoke({"topic": "agent graphs"})
    print(result.state["answer"])


if __name__ == "__main__":
    main()
