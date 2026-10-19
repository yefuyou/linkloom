# M3-I delivery and acceptance boundary

Status: the existing Product Ask can execute the integrated offline investigation
chain. Deterministic/Host acceptance is locally verified. Real-model semantics
and a real product business task are **NOT_EVALUATED**. This is not acceptance of
a complete intelligent business loop.

## Reproduce

From this branch's worktree, using Python 3.11+ with the repository dependencies:

```powershell
$env:PYTHONPATH = 'src'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
python scripts/run_m3i_product_acceptance.py --output work/m3i-my-run
```

The output directory must be new; previous evidence is never overwritten.
For the browser path, append `--serve-case continuity --port 8765`, open the
printed localhost URL, and ask the printed Chinese question. This uses the
ordinary Product server, `/api/product/ask`, and polling endpoint. It injects
explicit scripted adapters into the real services; it adds no demo backend or
alternate execution route. Repeated questions create new bounded runs. The
fixtures supply scripted model output, so this is a wiring demonstration only.
The normal product CLI has no live task-stage configuration and blocks task
investigation until an explicit Host configuration is supplied.

## Git provenance

Started from remote E `f9b009a0bb60f60f4895a4fdf2df069340ef8e47` in an isolated
worktree. Remote J is `eadf5ab1df473846695f4e10424d2e6e6c4a11cd`; remote K is
`dc00c46024e19085ca1ae0f21c6d0466d5e057ee`. Their common ancestor is
`31dde91f0e9b6015abc5754e995692a2f1db89ec`. Only the two J commits and two K
commits absent from E were integrated, by normal merges `bb0776c` and `24085f2`.
Their source changes are disjoint from E. No local unreviewed drafts were used.

## Executed chain and authority

`UnifiedAskService.ask` -> `ProductInvestigationService.start` -> repository
current-source snapshot -> `ProductTaskSources` -> existing F
`TaskObservationDiscoveryService` -> existing G `InvestigationPlanner` ->
Host admission of ready typed work -> `RuntimeRunBackend(task_relations)` ->
existing Runtime/ModelLoop/ToolRuntime/Ledger -> `inspect_task_context`,
`query_task_relations`, optionally existing `analyze_task_semantics` -> E
Working Context / original-span recovery -> existing result Grounding and
Reviewer -> product projection with original-source identities and uncertainty.

F/G are separately audited preparation calls, not invented Tool Ledger entries.
The Host never runs a tool executor itself. Only ready, unscoped relation
questions reach the existing semantic tool. A model-proposed SUPPORTED is not
a verified premise: dependent REVISES_PLAN/date work remains explicitly blocked.
Unsupported time/source scoping, clarification and inspection limits are exposed
as gaps. A ready discovery gap receives a Host UNKNOWN receipt over the actual
bounded F result, then one bounded G replan; F is not rerun as if absence were
proven. Generic Runtime planning is disabled for this workflow, preserving G's
ownership of task meaning.

Repository-issued source IDs, RawArtifact versions, exact spans and hashes agree
with the product catalog. Current-source validation occurs around F/G calls,
at Runtime/E reads, and before exposing the result. Removed/updated source
snapshots return BLOCKED without old quotes or tool trace. Summaries never
become evidence. No real Vault or formal task/Decision Memory was modified.

## Evidence levels

| Layer | Evidence | Result |
|---|---|---|
| Component/contract | Existing J/K, source identity, typed plan and runtime guard regressions in `evidence/offline.xml` | PASS locally |
| FakeModel product integration | Two original meetings, optional third conflicting meeting, original natural-language question, normal Product HTTP route | PASS for plumbing; PARTIAL/UNKNOWN business output |
| Real-model semantic test | No new Provider calls; prior J/K/E evidence retains its original scope | NOT_EVALUATED for M3-I |
| Real product business task | No real Provider-backed end-to-end business acceptance | NOT_EVALUATED |

The source texts describe continuation of log migration between meetings and a
deadline changing from June 20 to June 24. The question supplies neither task
IDs, answer entities nor tool order. Fake adapters independently supply the
scripted F/G/semantic and outer-loop responses. This does not demonstrate
autonomous model planning or semantic correctness.

## Results and budgets

Authoritative rerun artifacts are under `evidence/product-accepted/`:
`summary.json`, each case's `product-result.json`, and `runtime-audit.zip`.
The ZIP includes preparation requests/responses, raw sources, exact reference
mapping, Checkpoint, ModelLoop requests, Tool Ledger, recovery artifacts, traces
and actual Reviewer output. The synthetic `product.sqlite` inside each ZIP is
the source catalog; the generator also retains a local standalone copy.

| Case | Sources | F / G / semantic calls | Runtime turns / tools | Outcome |
|---|---:|---:|---:|---|
| continuity | 2 | 2 / 1 / 1 | 4 / 3 | PARTIAL; identity suggestion unconfirmed; revision premise missing |
| conflict | 3 | 3 / 1 / 1 | 4 / 3 | PARTIAL; model reports conflicting date evidence; all originals retained |
| missing candidates | 2 | 2 / 2 / 0 | 3 / 2 | UNKNOWN; absence not proven; clarification needed |
| replan | 2 | 2 / 2 / 1 | 4 / 3 | PARTIAL; UNKNOWN discovery receipt retained, then remaining identity question |

Each case's Runtime Reviewer accepted receipt/source integrity; it did not
approve semantic truth. No business conclusion or formal relation is published.

F defaults: 8 sources, 8,000 characters/16,000 bytes per source, 3 calls;
48 KiB maximum serialized request, 1,536 output tokens/call. The continuity
artifact reserves 19,045 conservative input units and 3,072 output tokens for F.
G: at most 3 session calls, 24,576 input/4,096 output tokens per call,
73,728/12,288 total input/output reservation, 12 subgoals, 8 relations and
3 relation hops. Continuity uses one G call with 13,102 conservative input
units and 4,096 output tokens reserved. Input units are byte-based upper bounds,
not measured tokenizer usage. Per-call simulated usage is retained separately.
Semantic tool: at most one batch, existing 32,768 input/8,192 output limits.
Outer Runtime: at most 12 model turns/12 Provider reservations. Stage budgets
are separate and visible; new billable requests/cost are 0 / $0.

## Verification

- `evidence/offline.xml`: 265 related offline tests passed (J/K, Runtime/E,
  task tools, Unified Ask, source mapping and initial M3-I controls).
- `evidence/http.xml`: 26 existing/new localhost HTTP tests passed, including
  deterministic current/historical queries and source investigations.
- `evidence/product-final.xml`: final 15 M3-I controls passed: natural question,
  missing/conflict/replan, removed/updated source, F/G budget, Provider gate,
  date scope, no second planner, E pressure/recovery/safe stop, and cold Runtime
  resume without repeating confirmed tool actions.
- `evidence/http-reviewed.xml`: two final HTTP tests passed, including repeated
  task questions and deterministic Fast Path with trap stage adapters.
- `evidence/publication-race.xml`: three source-publication tests passed,
  including revocation during result projection. Publication checks share the
  product store lock and revalidate after projection.
- `evidence/invalid-citation.xml`: a fabricated semantic span ID returns top-level
  BLOCKED with stage errors; it cannot become a publishable PARTIAL conclusion.
- `evidence/serve-smoke.json`: the actual `--serve-case` process served the
  normal product page and completed a new HTTP question with source evidence.
- `node tests/ui/unified_ask_dom_test.cjs`: PASS, including escaping and separate
  PARTIAL/UNKNOWN/BLOCKED displays and the absence caveat.
- `git diff --check`: PASS. No CI run, deployment or real-browser visual
  acceptance is claimed. Test counts overlap and must not be added together.

The offline tests use `-p scripts.evaluation_offline_plugin`; all external
network is denied. Localhost HTTP needed a sandbox exception for WinError 10013.
No paid Provider exception was requested or used.

Retained failures: `product-run-v1` failed at artifact writes because nested
Windows paths were too long; `review-repairs.xml` preserves the same failure
in a long test directory. Short operational run IDs and execution paths resolve
the demonstrated cases; `product-accepted` and final XMLs supersede these
execution attempts without rewriting them. An early legacy test attempted a
Hugging Face download; final related regressions use offline/BM25 settings.

## Remaining capabilities and acceptance decision

The following are unfinished, not hidden by passing component tests:

1. New Provider-backed F/G/Runtime semantics and real business end-to-end quality.
2. Independent semantic acceptance of a proposed relation; therefore dependent
   plan revision/date comparisons cannot safely continue from that suggestion.
3. General task understanding, exhaustive discovery, targeted expansion beyond
   the authorized source snapshot, or successful execution of every G operation.
4. Time/source-scoped relation execution: it is blocked rather than widened.
   Advanced decision `as_of` is rejected for task investigations before any call.
5. Automatic product job discovery/reopening after a complete server restart.
   The existing Runtime cold resume is verified with the current Host authority;
   no second checkpoint store is introduced. F/G preparation is not resumable.
6. Task adapters enabled in the default live product CLI. The available browser
   path is explicitly synthetic/offline. REPLAY injection is supported by the
   configuration contract, but a new M3-I real-output replay is not evaluated.
7. Very long Windows database parent paths still may hit OS path limits and
   safely block. Operational artifacts are retained, never automatically deleted.
8. Offline/no-payment execution relies on trusted Host injection of the scripted
   adapters. The configuration's FAKE/REPLAY string does not prove arbitrary
   Python objects cannot use a network. Default/live enablement needs a closed
   adapter capability/provenance boundary covering stage and outer models.

Independent read-only [review](REVIEW.md) found no remaining P1/P2 within this
boundary and recommended acceptance, commit and normal push. It did not accept
the unevaluated semantic/business capabilities above.

Host integration criteria and defined controls are met locally. Full intelligent
business-loop acceptance remains NOT_EVALUATED. The logical next step is a
separately authorized, budgeted real-model product task evaluation and a decision
on semantic review/premise acceptance; it is not a merge into master.

Reflection: a product route and durable tool evidence are now connected, while
semantic authority remains deliberately unresolved. Independent review caught
the accidental generic planner and a missing absence caveat; both have guards.
