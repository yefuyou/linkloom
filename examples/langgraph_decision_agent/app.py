"""A deterministic LinkLoom-style decision workflow built with LangGraph.

This is a reference example only. It does not replace or migrate production
Runtime V2 and performs no network, provider, vault, or external-store calls.
"""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt


class DecisionState(TypedDict, total=False):
    query: str
    source_evidence: list[dict[str, str]]
    memory_hint: str | None
    draft: dict[str, Any]
    status: str
    decision: str
    evidence_refs: list[str]
    tool_call_count: int
    events: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class DemoContext:
    workspace_id: str


class DeterministicDecisionModel:
    """Tiny fake model: it can only propose a decision present in source evidence."""

    def decide(
        self,
        query: str,
        source_evidence: list[dict[str, str]],
        memory_hint: str | None,
    ) -> dict[str, Any]:
        del query, memory_hint  # Memory is a navigation hint, never final evidence.
        if not source_evidence:
            return {"decision": None, "evidence_refs": [], "status": "insufficient_evidence"}
        item = source_evidence[0]
        return {
            "decision": item["decision"],
            "evidence_refs": [item["evidence_id"]],
            "status": "pending_review",
        }


def search_notes(query: str) -> list[dict[str, str]]:
    """Deterministic read-only tool over a tiny in-memory source fixture."""
    if "supplier" not in query.casefold():
        return []
    return [
        {
            "evidence_id": "ev:supplier:decision-b",
            "decision": "Supplier B",
            "quote": "The approved supplier is Supplier B.",
            "source_ref": "workspaces/demo/decisions/supplier.md#decision",
        }
    ]


def build_graph(store: InMemoryStore):
    model = DeterministicDecisionModel()

    def retrieve(state: DecisionState) -> dict[str, Any]:
        evidence = search_notes(state["query"])
        return {
            "source_evidence": evidence,
            "tool_call_count": state.get("tool_call_count", 0) + 1,
            "events": ["tool.search_notes"],
        }

    def read_memory(
        state: DecisionState,
        runtime: Runtime[DemoContext],
    ) -> dict[str, Any]:
        item = runtime.store.get((runtime.context.workspace_id, "decisions"), "current")
        value = item.value.get("decision") if item is not None else None
        return {"memory_hint": value, "events": ["memory.read"]}

    def draft_decision(state: DecisionState) -> dict[str, Any]:
        draft = model.decide(
            state["query"],
            state.get("source_evidence", []),
            state.get("memory_hint"),
        )
        return {"draft": draft, "events": ["model.fake_decision"]}

    def route_after_draft(state: DecisionState) -> str:
        return "review" if state["draft"]["status"] == "pending_review" else "end"

    def human_review(state: DecisionState) -> dict[str, Any]:
        response = interrupt(
            {
                "kind": "decision_review",
                "decision": state["draft"]["decision"],
                "evidence_refs": state["draft"]["evidence_refs"],
            }
        )
        approved = isinstance(response, dict) and response.get("approved") is True
        return {
            "status": "approved" if approved else "rejected",
            "events": ["review.approved" if approved else "review.rejected"],
        }

    def route_after_review(state: DecisionState) -> str:
        return "persist" if state["status"] == "approved" else "end"

    def persist_approved_decision(
        state: DecisionState,
        runtime: Runtime[DemoContext],
    ) -> dict[str, Any]:
        draft = state["draft"]
        runtime.store.put(
            (runtime.context.workspace_id, "decisions"),
            "current",
            {
                "decision": draft["decision"],
                "evidence_refs": draft["evidence_refs"],
                "approval": "human-approved-demo",
            },
        )
        return {
            "decision": draft["decision"],
            "evidence_refs": draft["evidence_refs"],
            "events": ["memory.persist_approved_decision"],
        }

    builder = StateGraph(DecisionState, context_schema=DemoContext)
    builder.add_node("retrieve", retrieve)
    builder.add_node("read_memory", read_memory)
    builder.add_node("draft_decision", draft_decision)
    builder.add_node("human_review", human_review)
    builder.add_node("persist_approved_decision", persist_approved_decision)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "read_memory")
    builder.add_edge("read_memory", "draft_decision")
    builder.add_conditional_edges(
        "draft_decision",
        route_after_draft,
        {"review": "human_review", "end": END},
    )
    builder.add_conditional_edges(
        "human_review",
        route_after_review,
        {"persist": "persist_approved_decision", "end": END},
    )
    builder.add_edge("persist_approved_decision", END)
    return builder.compile(checkpointer=InMemorySaver(), store=store)


def run_demo() -> dict[str, Any]:
    store = InMemoryStore()
    workspace_id = "demo-workspace"
    namespace = (workspace_id, "decisions")
    store.put(namespace, "current", {"decision": "Supplier A", "evidence_refs": ["ev:supplier:decision-a"]})
    app = build_graph(store)
    context = DemoContext(workspace_id=workspace_id)

    review_thread_id = "langgraph-reference-review"
    review_config = {"configurable": {"thread_id": review_thread_id}}
    paused = app.invoke(
        {"query": "Which supplier is approved?", "tool_call_count": 0, "events": []},
        review_config,
        context=context,
    )
    interrupted = bool(paused.get("__interrupt__"))
    if not interrupted:
        raise RuntimeError("expected the approval node to interrupt")

    resumed = app.invoke(
        Command(resume={"approved": True}),
        review_config,
        context=context,
    )

    second_thread_id = "langgraph-reference-recall"
    second_config = {"configurable": {"thread_id": second_thread_id}}
    second_paused = app.invoke(
        {"query": "Which supplier is approved?", "tool_call_count": 0, "events": []},
        second_config,
        context=context,
    )
    second_snapshot = app.get_state(second_config)
    cross_thread_memory_hint = second_snapshot.values["memory_hint"]
    if second_paused.get("__interrupt__"):
        app.invoke(Command(resume={"approved": False}), second_config, context=context)

    return {
        "interrupted": interrupted,
        "review_thread_id": review_thread_id,
        "second_thread_id": second_thread_id,
        "resumed_state": resumed,
        "cross_thread_memory_hint": cross_thread_memory_hint,
    }


def main() -> int:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
