# Unified multi-hop investigation delivery

Base: `5be2e14559a1fbec3f37052d38be97fadfbf5335`; branch
`codex/linkloom-unified-investigation`. Git verified that multi-hop commit
`70052cd3299c50262d8306353db5840cd1b05036` is an ancestor of this base.
Origin: `https://github.com/yefuyou/linkloom.git`. Other worktrees were preserved.

## Actual product behavior

All six requests were made from the real Unified Ask page against synthetic
workspaces; the HTTP fixture independently exercises the same product endpoint.

| Workspace / question | Observed result / execution |
| --- | --- |
| Atlas / 我们现在采用哪家供应商？ | Wrenwell; DETERMINISTIC; zero model calls; original receipt/evidence |
| Atlas / 为什么 Birchline 被 Wrenwell 替换？ | Temporal AGENT; replacement verified, reason UNKNOWN; 5 model turns |
| Relations / Orion 项目负责人的直属领导是谁？ | 陈宁; project → 林舟 → 陈宁; 8 turns, 2 searches + 5 reads |
| Relations / Who owns the project behind Copper Meadow and who leads that owner? | Owen Brook; alias → Falcon Reach → Ada Vale → Owen Brook; 11 turns, 3 searches + 7 reads, three cited documents |
| Relations / As of 2026-10-06, Who is the direct leader of the owner of Mariner Field? | Tess Avery retained as owner; Casey North / Devon Hart conflict; null leader; conflicting_evidence |
| Relations / Who is the direct leader of the owner of Harbor Gap? | Riley Fox retained as owner; null leader; insufficient_evidence |

Source results say **source-supported relationship**, never formally approved
business decision. Existing team-decision-result/v1 is reused. Partial results
retain validated rationale facts and a null main decision. Both opposing verified
passages are visible for conflict; unrelated reads are collapsed. Reviewer PASS
means the bounded output is evidence-valid, not that the conflict is resolved.

## Actual three-hop execution and artifacts

Final local HTTP run: `ui-884344be9f7f`; Runtime `run_p4_a782309df70c`;
thread `ui-thread-961dde42f021`; Reviewer `review_3394715d`.
Local evidence root (ignored, not pushed):
`work/unified_investigation/final-product-a37e0534/`.
`three.json` is the actual product response; `three-runtime/` contains the
synthetic runtime snapshot. Trace below means
`three-runtime/traces/run_p4_a782309df70c/events.jsonl`; result means
`three-runtime/runtime/results/run_p4_a782309df70c/result.json`.
Model turn artifacts are under
`three-runtime/runtime/models/model/run_p4_a782309df70c/run_p4_a782309df70c_turn_N/`.

| Step | Real source / function | Checkable evidence |
| --- | --- | --- |
| HTTP → router | ui/server.py do_POST; ui/unified_ask.py ask / _source_request | three.json original query, path and route.reason |
| Router → TaskPlan | runtime/team_decision_planner.py plan_relation_investigation | route.plan has only root alias and dependent placeholders |
| Product → RunRequest | ui/investigation.py start; ui/backend.py start / _execute | runtime/request_run_p4_a782309df70c.json explicit TaskPlan, team-decision-result/v1, max_steps=12 |
| Runtime → ModelLoop | runtime/graph.py start_multi_agent; agents/runtime_adapter.py run; agents/retrieval_agent.py execute; runtime/model_loop.py run / _run_durable | Trace seq1–6; model turn1/request.json |
| Model → ToolRuntime | model_loop.py execute_tool_action; tools/runtime.py ToolRuntime.execute | Trace seq7–12: search alias then verified read; result.tool_ledger records1–2 |
| ToolResult → Observation | model_loop.py execute_tool_action persists ToolResult | turn2/observation.json holds the verified alias passage; turn3/request.json observation_ref points to it |
| Next model → verified binding | runtime_adapter.py _planned_search_query; task_plan.py verified_dependency_entities | Ledger3 search dependency alias=Falcon Reach after read2; ledger6 dependency owner=Ada Vale after reads4–5; Trace seq13–24 |
| Final → Validator | retrieval_agent.py _project_result; task_plan.py validate_relation_final; agents/claim_grounding.py assess_claim_text | turn11/response.json typed Final; three per-hop refs; result.result.team_decision |
| Reviewer | agents/reviewer_agent.py execute via Coordinator.run | Trace seq34–36; result.review review_3394715d, three citation checks + subject check PASS |
| Product → UI polling | ui/investigation.py inspect / _source_relations; ui/unified_ask.py inspect / _from_snapshot; product.js pollAsk / renderSourceInvestigation | three.json facts, source/version/line/hash identity and safe tool trace; real browser screenshot work/three-hop-browser-final.jpg |

Actual Ledger dependency audit reconstructs each preceding prefix and rejects
invented downstream entities. Citation refs end in `820a9c6` (relation-2.md),
`49f9fd` (relation-3.md), `dedc40` (relation-4.md); full immutable identities,
source versions, line 1 and content/quote hashes remain in three.json and Ledger.
There is no Oracle/Gold input to production Runtime. The fixture uses an existing
FakeModel controller only to choose real ToolRuntime actions from observations.

Source budget is 1–12 configurable model/provider turns; Temporal stays 8.
Existing retrieval identity cap=12, Coordinator tool cap=20, task deadline=5000ms
and context bounds remain. Source run used 11 model turns, 10 retrieval tools;
Coordinator usage also counts review/publication (12 steps / 14 tools), so these
are distinct counters. Peak bounded context was 29,996 bytes / estimated 7,499
tokens (local byte estimate); complete serialized request peak 31,207 bytes.
No request approached the existing 96KiB context cap. A configured 7-turn source
budget demonstrably stops the three-hop run. No new automatic retries were added.

## Conflict defect and independent review

HOLD-G's verified reporting facts survived search/read, but the conflict helper
only recognized replacement/owner relations. TaskPlan therefore reported missing
evidence instead of a reporting conflict. The fix is in Claim Grounding and
TaskPlan assessment: reports_to/direct_leader_of share directed conflict logic;
the verified parent supplies subject, project and minimum date; incompatible
values must share snapshot/date-kind/project and distinct source identities.
Unverified hits, other people/projects and different dates cannot create conflict.
Validator requires both opposing refs in uncertainty; UI only projects this
validated result. Three-hop conflict also preserves the alias and owner prefix.

Independent review found source Runtime inherited Decision Memory search and
candidate capture. A default-on existing Runtime capability switch now disables
both only for product source runs; project recall is also disabled. No second
store or Runtime was created. A same-workspace authorized-memory + single-passage
chain test proves no Memory tool is exposed, direct access fails, capture is not
called, Decision/candidate/events remain unchanged, and no Memory DB is created.
Targeted independent re-review closed this P1 and found no remaining blocker.
Current source UI has no cross-process resume endpoint; future resume work must
preserve the disabled policy when reconstructing an engine.

## Acceptance, limitations and reproduction

- Focused product/runtime suite: **157 passed** (107 unit, 43 real HTTP, 7 Runtime
  integration). TaskPlan/Grounding focused unit contracts: **61 passed**, 30
  history-dependent cases deselected because their ignored phase9/11/14 fixtures
  are unavailable; no replacement Gold was fabricated.
- Broader old Memory integration checks: 2 passed / 6 failed. All six failures
  reproduced on the exact untouched 5be2e14 baseline extracted read-only under
  ignored work/unified_investigation/baseline-memory-regression. Five old capture
  cases fail with search source-context mismatch; one old memory injection case
  expects an event the current approved-workspace recall policy does not emit.
  They are baseline failures, not fixed in this feature. Combined run: 159 passed,
  6 baseline failures. This is not a whole-repository green claim.
- JS syntax and four DOM groups pass: request/date invalidation, latest request
  wins, workspace isolation, permission reset, feedback mutation race, source
  trust/partial/conflict and unaccepted Final suppression.
- Real browser: six answer scenarios above, HOLD-H unsupported, Feedback
  BEFORE→PENDING→APPROVED(Wrenwell, zero model)→REVOKED, no-permission refusal,
  FakeTransport UNCERTAIN (one request, no Final), rapid question/date/workspace
  changes with no stale answer overwrite. Source edits invalidate old HTTP Finals.
- Old development set 11/11; exposed heldout replay 9/10 (HOLD-G fixed; HOLD-H
  planning_failed). **Original first independent score remains 8/10**. Frozen Git
  blob SHA256 remains cbfde841b517a3d2f78b59a14167386b725ae3d3de603ffab490b2bdef43152f.
  No new independent generalization set or score. No historical results changed.
- Runtime execution, binding, references and states are verified offline;
  real Gemini/generalization is unverified. **Real paid Provider requests: 0.**
- Only project→owner→leader and alias→project→owner→leader in the existing bounded
  grammar; one generic Chinese project form added. The long Chinese alias query
  and HOLD-H mixed expression remain unsupported. Exact snapshot dates are
  supported; no inferred validity intervals, arbitrary graph reasoning, multi-slot
  formal decision reasoning or cross-workspace joins. Unknown relation wording
  never falls into free-form Temporal Agent answering. Feedback terms remain
  restricted to their approved decision-discovery scope.
- Production: 9 files, net +235 lines; dependencies +0; Runtime abstractions +0.
  Existing Runtime/ToolRuntime/Memory/Reviewer reused. Formal authority unchanged.

```powershell
$env:PYTHONPATH='src;tests/integration'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
python tests/integration/run_unified_ask_demo.py --source-investigation --port 8804
```

Select Atlas for decisions, Relations for source chains; open Ask. Everything is
synthetic and FakeModel-only. `--require-agent-permission --offline-scenario uncertain
--port 8805` starts the offline permission/UNCERTAIN demonstration. This is local
acceptance, not deployment or a formal release. Raw synthetic Ledger/trace and
browser captures stay in ignored work/ and are not pushed.
