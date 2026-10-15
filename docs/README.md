# LinkLoom developer map

The current product is **Team Decision Recovery / Temporal Decision
Intelligence**, with one Unified Ask form. The authoritative direction is
[PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md); the earlier personal-vault milestones
and completed implementation packets are historical context.

## Run the product

```powershell
python -m pip install '.[dev]'
$env:PYTHONPATH = 'src'
python -m linkloom.ui --database work/product.sqlite
```

Open `http://127.0.0.1:8765/`. Source import, reviewed memory and deterministic
Ask are local; semantic extraction and the temporal Agent require explicit
external-provider permission and a configured provider. The separate CLI is
`python -m linkloom --help`; its scan/ask/connect commands remain supported.

## Read these eight files in order

| File | Responsibility |
|---|---|
| [ui/__main__.py](../src/linkloom/ui/__main__.py) | Process setup, local SQLite ownership and explicit Provider/Agent opt-in. |
| [ui/server.py](../src/linkloom/ui/server.py) | Product HTTP routes, request limits and Unified Ask wiring; legacy endpoints are retained. |
| [ui/unified_ask.py](../src/linkloom/ui/unified_ask.py) | Bounded deterministic/Agent routing; pins workspace, subject, relation and valid-time date. |
| [ui/product.py](../src/linkloom/ui/product.py) | Sources, candidate Review, Decision Memory and Timeline; coordinates stores and source reconciliation. |
| [semantic_ingestion/materialization.py](../src/linkloom/semantic_ingestion/materialization.py) | Reviewed candidate lifecycle, exact authorization receipts and authoritative materialization. |
| [decision_memory/store.py](../src/linkloom/decision_memory/store.py) | Workspace-scoped temporal decisions, provenance, supersession and bounded queries. |
| [ui/investigation.py](../src/linkloom/ui/investigation.py) | Temporal Agent setup, frozen source versions, bounded runtime jobs and verified result projection. |
| [runtime/model_loop.py](../src/linkloom/runtime/model_loop.py) | Durable model/tool execution, Ledger outcomes, Grounding and Reviewer before returning a Final. |

```text
Sources -> Semantic Ingestion -> candidates -> Review / authorization
        -> Decision Memory / Evidence
User -> Unified Ask -> deterministic lookup -> Decision Memory / Timeline
                    -> temporal Agent -> Runtime / tools -> Evidence
                                      -> Validator / Reviewer -> answer
```

## Ownership and dependencies

| Area | Where to look | Boundary |
|---|---|---|
| Source versions and trusted Evidence | `semantic_ingestion/`, `decision_memory/sources.py`, `agents/runtime_evidence.py` | Source identity and content versions are validated; extraction alone has no authority. |
| Agent and tool permissions | `runtime/`, `tools/`, `agents/reviewer_agent.py`, `agents/claim_grounding.py` | Review and Grounding stay mandatory; uncertain requests cannot produce an accepted Final. |
| External Providers | `agents/providers/` | SDK construction is explicit and lazy; Provider and tool Ledgers protect request/replay outcomes. |
| Generic Agent Memory | `memory/` | Approved preferences and project terminology; different responsibility from temporal business decisions. |
| Retrieval and context | `retrieval.py`, `retrieval_v2/`, `indexing/`, `context/` | Old and V2 paths have active callers and contracts; neither is disposable by name. |
| Local note output | `scanner.py`, `services/`, `text_output.py` | Read-only inputs, separate generated reports and shared atomic UTF-8 writes. |

Direct product dependencies are `PyYAML` and `rank-bm25`. Gemini SDK (`smoke`),
vector models (`retrieval`) and pytest (`dev`) are optional extras. Setuptools
ships only `src/linkloom`; top-level benchmarks, scripts and relation evaluation
are not installed product packages. Normal UI startup does not import research
runners, model SDKs or vector models. Runtime imports further tools lazily.

## Research and history: read only when relevant

- **OPTIONAL research:** `benchmarks/`, `relation_eval/`, `examples/`,
  `src/linkloom/evaluation/`, `src/linkloom/experiments/`; retrieval V2
  `benchmark.py`, `characterization.py`, `downstream_comparison.py`, `metrics.py`;
  benchmark/report scripts. The explicit evaluation CLI remains supported.
- **ARCHIVE context, kept in place:** `docs/implementation/`, earlier
  `docs/requirements/` packets, `docs/evaluation/` and `output/playwright/`.
  Frozen Gold, scores, seals, Provider Ledgers and Traces remain audit evidence.
- **KEEP contracts:** current [Unified Ask SPEC](requirements/unified-ask-experience/SPEC.md),
  [Semantic Ingestion SPEC](requirements/semantic-ingestion/SPEC.md),
  [temporal integration notes](TEMPORAL_AGENT_INTEGRATION.md), root
  [SPEC](../SPEC.md), [DEV_SPEC](../DEV_SPEC.md) and [Agent rules](../AGENTS.md).
- **Process references:** [role boundaries](MULTI_AGENT_ROLES.md),
  [SPEC method](SPEC_DRIVEN_DEVELOPMENT.md),
  [acceptance checklist](ACCEPTANCE_CHECKLIST.md), [Git policy](GIT_WORKFLOW.md).

## Focused offline acceptance

```powershell
$env:PYTHONPATH = 'src'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
python -m pytest tests/unit/test_unified_ask.py tests/integration/test_unified_ask_flow.py tests/unit/test_temporal_decision_memory.py tests/unit/test_structured_temporal_lookup.py tests/smoke/test_temporal_product_http_lifecycle.py -q
node tests/ui/unified_ask_dom_test.cjs
```

These use synthetic sources/FakeTransport. Full baseline collection has four
old failures: three MemoryAgentBench tests import a missing G3 script and Stage
C imports a missing loop script. Some older replay tests additionally require
untracked historical `work/` packets or use superseded model/source contracts.
Keep those failures separate from the focused product result; do not delete
necessary tests or alter Gold/validation to make collection look green.

Local verification/build files belong under ignored `work/`; generated
`*.egg-info/`, caches and database sidecars are also ignored.