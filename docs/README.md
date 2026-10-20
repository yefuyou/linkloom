# LinkLoom developer map

Start with the [product README](../README.md), then the
[product architecture](architecture/PRODUCT_ARCHITECTURE.md) and
[runtime architecture](architecture/RUNTIME_ARCHITECTURE.md). They describe the
merged product at `95a125ce4d1b2867102217e1d0430044fbf4f739`, including bounded
Recovery and grounded partial results; historical
architecture notes remain below. The [roadmap](PRODUCT_ROADMAP.md) owns product
direction, and [AGENTS.md](../AGENTS.md) owns contribution boundaries.

## Run and find the product entry points

Follow [Quickstart](../README.md#quickstart) for no-key startup, semantic
extraction and the additional investigation opt-in. Source extraction needs an
explicitly configured provider; reviewed-state lookup is deterministic. The
separate CLI remains available through `python -m linkloom --help`.

| Read in order | Responsibility |
|---|---|
| [ui/__main__.py](../src/linkloom/ui/__main__.py) | Process entry, database path, lazy Gemini SDK setup and explicit provider/Agent permissions. |
| [ui/server.py](../src/linkloom/ui/server.py) | HTTP boundary, request limits, static UI, Unified Ask and retained legacy routes. |
| [ui/product.py](../src/linkloom/ui/product.py) | Source versions, Review, materialization, Decision Memory and source reconciliation. |
| [ui/unified_ask.py](../src/linkloom/ui/unified_ask.py) | Deterministic routing to reviewed state, temporal investigation or supported source relations. |
| [ui/scope_discovery.py](../src/linkloom/ui/scope_discovery.py) | Resolve trusted decision slots and query dates; ambiguous scope remains explicit. |
| [ui/investigation.py](../src/linkloom/ui/investigation.py) | Freeze source versions, pin run scope and project only verified investigation results. |
| [ui/backend.py](../src/linkloom/ui/backend.py) | Start/inspect bounded background runtime jobs; expose actual Reviewer disposition. |

The reviewed-state route does not call an LLM. Investigation uses the runtime
below; source-relation runs explicitly disable decision/generic memory. The
current UI keeps investigation jobs in memory with temporary files, so runtime
durability should not be read as a cross-process UI recovery guarantee.

## Follow the Agent loop

| Step | Source | What to inspect |
|---|---|---|
| Runtime ownership | [runtime/graph.py](../src/linkloom/runtime/graph.py) | RuntimeEngine construction, state/checkpoint ownership, start and resume. |
| Wiring | [agents/runtime_adapter.py](../src/linkloom/agents/runtime_adapter.py) | Verified callbacks, workspace scope, tools, context assembly and memory-candidate capture. |
| Orchestration | [agents/coordinator.py](../src/linkloom/agents/coordinator.py) | Deterministic task flow and Reviewer gate; Curator runs for `connect`. |
| Retrieval wrapper | [agents/retrieval_agent.py](../src/linkloom/agents/retrieval_agent.py) | Model-loop setup, selected Final protocol, claim/temporal validation and observed evidence. |
| Model loop | [runtime/model_loop.py](../src/linkloom/runtime/model_loop.py) | Durable requests/proposals, ordered tools, provider continuation, context visibility, budgets and termination. |
| Model boundary | [agents/model_adapter.py](../src/linkloom/agents/model_adapter.py), [providers/](../src/linkloom/agents/providers/) | Provider-neutral contracts, actual SDK invocation, usage and uncertain outcomes. |
| Tools | [tools/runtime.py](../src/linkloom/tools/runtime.py), [tools/tool_policy.py](../src/linkloom/tools/tool_policy.py) | Argument validation, permissions, pending-before-execution durability and call budget. |
| Persistence / resume | [runtime/checkpoint.py](../src/linkloom/runtime/checkpoint.py), [tools/ledger.py](../src/linkloom/tools/ledger.py), [runtime/recovery.py](../src/linkloom/runtime/recovery.py), [runtime/recovery_policy.py](../src/linkloom/runtime/recovery_policy.py) | Durable result reuse versus verification-required outcomes; policies do not themselves grant retry authority. |
| Progress / Partial Final | [runtime/progress.py](../src/linkloom/runtime/progress.py), [runtime/recovery_policy.py](../src/linkloom/runtime/recovery_policy.py), [ui/projection.py](../src/linkloom/ui/projection.py) | TaskPlan v2 exhaustion can request cited partials; identity/permission failures and model/provider run limits reached before a valid Final remain failures. Grounding and Reviewer still gate acceptance. |

## Retrieval, evidence and the different kinds of memory

| Concern | Source entry | Boundary |
|---|---|---|
| Retrieval | [retrieval_v2/runtime_backend.py](../src/linkloom/retrieval_v2/runtime_backend.py), [retrievers.py](../src/linkloom/retrieval_v2/retrievers.py), [indexing/](../src/linkloom/indexing/) | BM25/dense/hybrid and directory scope; optional embedding dependencies. The older [retrieval.py](../src/linkloom/retrieval.py) still has active callers. |
| Context | [context/assembler.py](../src/linkloom/context/assembler.py), [context/manifest.py](../src/linkloom/context/manifest.py) | Budgeted selection, stale/provenance filters and source/index lifecycle; not an LLM summarizer. |
| Source evidence | [semantic_ingestion/artifacts.py](../src/linkloom/semantic_ingestion/artifacts.py), [evidence.py](../src/linkloom/semantic_ingestion/evidence.py), [decision_memory/sources.py](../src/linkloom/decision_memory/sources.py) | Immutable versions and exact passage identity. |
| Extraction → Review | [semantic_ingestion/pipeline.py](../src/linkloom/semantic_ingestion/pipeline.py), [extraction.py](../src/linkloom/semantic_ingestion/extraction.py), [materialization.py](../src/linkloom/semantic_ingestion/materialization.py) | LLM extraction produces candidates; human/policy authorization and freshness checks control durable facts. |
| Temporal Decision Memory | [decision_memory/store.py](../src/linkloom/decision_memory/store.py), [reconciliation.py](../src/linkloom/decision_memory/reconciliation.py) | Workspace-scoped validity, supersession, actions/owners, provenance and stale state. |
| Generic Agent Memory | [memory/](../src/linkloom/memory/), [ui/feedback_memory.py](../src/linkloom/ui/feedback_memory.py) | Reviewed preferences/terminology and product feedback lifecycle, not decision truth or citation evidence. |
| Runtime candidate capture | [agents/memory_candidate.py](../src/linkloom/agents/memory_candidate.py) | Reviewer-passed result can become a candidate; separate authorization still required. |
| Grounding / Reviewer | [agents/team_decision.py](../src/linkloom/agents/team_decision.py), [claim_grounding.py](../src/linkloom/agents/claim_grounding.py), [reviewer_agent.py](../src/linkloom/agents/reviewer_agent.py) | Verify observed evidence, hashes, quotes and supported claim forms; Reviewer is deterministic Python. |
| Supported relation plans | [runtime/team_decision_planner.py](../src/linkloom/runtime/team_decision_planner.py), [task_plan.py](../src/linkloom/runtime/task_plan.py) | Bounded templates and dependencies, not unrestricted reasoning. |

## Focused offline verification

From the checkout root, after installing the editable package:

```bash
python -m pip install -e ".[dev]"
```

In PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
python -m pytest tests/unit/test_unified_ask.py tests/integration/test_unified_ask_flow.py tests/unit/test_temporal_decision_memory.py tests/unit/test_structured_temporal_lookup.py tests/smoke/test_temporal_product_http_lifecycle.py -q
node tests/ui/unified_ask_dom_test.cjs
```

For the integrated Recovery boundary, run these additional provider-free contracts:

```powershell
python -m pytest tests/unit/test_graceful_partial_recovery.py tests/unit/test_progress_assessment.py tests/unit/test_recovery_policy.py tests/unit/test_recovery_passage_coverage.py tests/unit/test_runtime_v2_multi_action.py tests/unit/test_context_assembler.py -q
```

These focused contracts use synthetic data/FakeTransport, without provider
requests. Additional tests sit beside the source anchors in the two architecture
maps. Do not interpret a focused pass as repository-wide or CI acceptance.
For dated acceptance context see [integration status](INTEGRATION_STATUS.md).
Some research tests depend on unshipped scripts or historical local packets.
Verification artifacts belong
under ignored `work/`.

## Contracts, history and research

- Current contracts: [Unified Ask SPEC](requirements/unified-ask-experience/SPEC.md),
  [Semantic Ingestion SPEC](requirements/semantic-ingestion/SPEC.md),
  [temporal integration notes](TEMPORAL_AGENT_INTEGRATION.md),
  [product SPEC](../SPEC.md) and [development SPEC](../DEV_SPEC.md).
- Product demonstration: [walkthrough](V1_DEMO.md),
  [release notes draft](V1_RELEASE_NOTES_DRAFT.md),
  [backlog](V1_1_BACKLOG.md),
  [evidence matrix](requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md).
- Architecture history: [architecture directory](architecture/),
  [LangGraph mapping](architecture/LANGGRAPH_MAPPING.md),
  [implementation notes](implementation/). Existing documents are retained;
  use their dates and source baseline when reading them.
- Research/reference code: [benchmarks](../benchmarks/),
  [relation evaluation](../relation_eval/), [examples](../examples/),
  [evaluation code](../src/linkloom/evaluation/) and
  [experiment code](../src/linkloom/experiments/). These are separate from
  the normal UI startup path; frozen Gold, seals, ledgers and traces are evidence.
- Process references: [role boundaries](MULTI_AGENT_ROLES.md),
  [SPEC method](SPEC_DRIVEN_DEVELOPMENT.md),
  [acceptance checklist](ACCEPTANCE_CHECKLIST.md) and [Git policy](GIT_WORKFLOW.md).

## Cross-meeting task investigation integration

[Integration SPEC](requirements/m3-p-task-investigation/SPEC.md) and
[dependency audit](requirements/m3-p-task-investigation/DEPENDENCIES.md) define
the task observation/proposal/P4 read boundary. The normal CLI exposes an explicit
`--offline-task-demo` using the same Ask HTTP routes and deterministic synthetic
stages; real task Provider stages remain unavailable. Task results have saved
read-only validation; temporal/source-relation jobs retain their existing lifecycle.

Follow `ui/task_investigation.py` (F/G/source authorization),
`tools/task_relations.py` (read tools/P4), `agents/task_relation_result.py`
(receipt grounding/Reviewer), and `ui/task_result.py` (display). These extend
RuntimeEngine/ModelLoop/ToolRuntime rather than adding another loop.
