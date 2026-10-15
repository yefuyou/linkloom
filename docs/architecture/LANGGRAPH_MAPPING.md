# LinkLoom Runtime V2 ↔ LangGraph Mapping

The executable reference in [`examples/langgraph_decision_agent/`](../../examples/langgraph_decision_agent/)
demonstrates how the same decision workflow concepts map to LangGraph. It is a
deterministic, in-memory example; production Runtime V2 has not been migrated.

| LinkLoom concept | LangGraph counterpart | Reference example |
| --- | --- | --- |
| `RuntimeEngine` | Compiled `StateGraph` runtime | `build_graph()` compiles a typed graph. |
| Runtime state | Typed graph `State` | `DecisionState` carries query, source evidence, draft, and status. |
| Durable checkpoint | Checkpointer | `InMemorySaver` checkpoints the paused graph. |
| Run identity | `thread_id` | Resume uses the same review thread ID; a second ID starts a separate run. |
| `ToolRuntime` | Tool node | The `retrieve` node executes deterministic `search_notes`. |
| Runtime routing | Conditional edge | Draft status and human approval select the next node. |
| Runtime state accumulation | State reducer | `events` uses `operator.add` to retain events from each node. |
| Long-term context | Store / external context layer | `InMemoryStore` is scoped by workspace and shared across threads. |
| Governance | Interrupt + persisted review | `interrupt()` pauses for approval; only an approved resume writes the decision. |

## What this example proves

- A typed graph can route from retrieval through a deterministic decision draft
  and a persisted human-review interrupt.
- The checkpointer resumes the same run with its original `thread_id`.
- A separate thread can retrieve a workspace-scoped Store hint.
- The fake model cites only evidence returned by the source-search tool;
  memory is navigation context and cannot ground the final decision.
- A rejected or interrupted proposal is not materialized as approved memory.

Run it from the repository root with the instructions in the example README.
The integration test verifies interrupt/resume, tool execution, reducer output,
conditional routing, and cross-thread Store access without a real model provider
or user vault.

## Build-versus-buy boundary

LinkLoom's custom Runtime V2 was built to validate multi-action durability,
partial resume, provider continuation, and evidence visibility. This example
does not establish that a production migration is necessary or beneficial.
For productionization, the team can re-evaluate whether generic orchestration
should move to LangGraph while keeping LinkLoom's evidence, workspace-security,
and decision contracts as application-owned boundaries.
