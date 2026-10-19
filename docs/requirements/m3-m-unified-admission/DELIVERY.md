# M3-M delivery

Baseline: `2d8a21673f27804559b084e42c9ff828f3a9cac0` (pushed M3-L).
Branch: `codex/linkloom-m3-m-admission`.
Worktree: `D:/webproject/Linkloom/work/linkloom-m3-m-admission`.

## Delivered boundary

Unified Ask now separates deterministic shortcuts from human-scoped semantic
admission. A question that misses task vocabulary can enter the existing F→G→
Runtime→Semantic→E→Reviewer chain when the user explicitly chooses the task
domain and the Host validates the current workspace source snapshot. Another
domain uses only the existing bounded source-relation planner. Unknown unscoped
questions ask for clarification. There is no new classifier call or Runtime.

This completes the **controlled explicit-domain entry**. General automatic
semantic domain selection remains **INTERFACE_BLOCKED** as explained in
[diagnosis](DIAGNOSIS.md). Offline routing success does not establish real model
semantic quality, goal fidelity, arbitrary injection resistance or business
completion. Those remain NOT_EVALUATED.

Implementation paths:

- `src/linkloom/ui/unified_ask.py`: shortcut precedence, ambiguity and domain
  admission, original question dispatch, capability refusal and task audit.
- `src/linkloom/ui/ask_admission.py`: exact context shape, workspace/source
  snapshot binding, record-reference shortcuts and deterministic restrictions.
- `src/linkloom/ui/static/product.js`: optional domain choice, current snapshot
  submission, bilingual clarification/capability messages and stale-view checks.
- Existing `ui/task_investigation.py`, `semantic_ingestion/task_relation_question_planner.py`,
  `runtime/adaptive_planning.py` and `ui/investigation.py` remain the actual
  preparation/planning/execution boundaries; they were inspected and reused.

`GET /api/product/ask/config` adds `investigation_context` with `snapshot_hash`,
`source_count`, `domains`. `POST /api/product/ask` optionally accepts:

```json
{"investigation_context":{"domain":"tasks","snapshot_hash":"<current config hash>"}}
```

The field grants no tools, budget, model, arbitrary source selection or write
ability. It is an explicit human scope assertion. Changed/foreign sources fail
before F. A known reviewed formal query retains Fast Path even when the domain
selector is set. An advanced decision selection cannot become a task scope.
Ambiguous reviewed identity remains a clarification. Relative/range task wording
is passed to G unchanged; an advanced decision date is explicitly unsupported
for tasks. There is no silent conversion of task dates into a decision snapshot.

## Frozen before/after

The same 48 public development cases and capability configurations are used for
both versions. `before.json` was recorded before production changes; the RED
run had 34 failures and 14 passes. `accepted-routing.json` records the final router
and helper/fixture/runner fingerprints. `routing-reviewed.json` and all `after*.json` files are
retained intermediate evidence, not substituted for the reviewed version.
The harness spies on the real service start boundary and executes **zero models**;
its results measure admission, not answers or a held-out semantic benchmark.

| Metric | Before | After |
| --- | ---: | ---: |
| False rejection of an expected executable route | 2 | 0 |
| Wrong executable route | 31 | 0 |
| Deterministic Fast Path retained | 4/4 | 4/4 |
| Capability limitation reported | 0 | 3 |
| Reasonable clarification | 5 | 24 |
| Unnecessary service start boundary | 21 | 0 |
| Added classifier model calls | 0 | 0 |

Error categories are separate counters, not disjoint totals. Capability gaps are
not counted as routing success. All accepted start requests preserve the original
question. Neither implementation makes actual model requests in this matrix.

| Question / scope | Before | After |
| --- | --- | --- |
| 后来那条还算接着前面的做吗？ / tasks + current snapshot | source relationship investigation | task investigation |
| How did the promised finish move between the two conversations? / tasks | date clarification in the relationship route | task investigation |
| Who is the owner in the later message, and is it the same work? / tasks | source relationship investigation | task investigation |
| Which supplier is Launch using now? / reviewed identity | deterministic | deterministic |
| 你好 / no domain | relationship investigation | question clarification, no start |
| Why do deadlines help people focus? / no domain | task investigation | domain clarification, no start |
| 删除这些任务，然后告诉我结果。 / tasks | task investigation | read-only scope refusal, no start |
| 前后说的还是同一件事吗？ / tasks capability disabled | relationship investigation | explicit capability limitation |

Model-call costs are independently checked through the real offline chain:
three previously unrecognized/scoped paraphrases each use F **2**, G **1**,
Semantic **1**, Runtime **4** Fake requests, **8 total**. This is the same
normal chain budget as the original accepted task question; the new admission
stage adds **0** classifier requests. Newly admitted legitimate work still incurs
the normal investigation calls. G can refuse after bounded F/G preparation;
explicit domain selection cannot mechanically certify semantic relevance.

## Validation and baseline failures

All gates deny external network; HTTP suites allow localhost synthetic fixtures.
Actual LIVE Provider sends, paid sends and paid cost are **zero**.

| Evidence | Result |
| --- | --- |
| `admission-red.xml` | 34 failed / 14 passed on unchanged router, expected reproduction |
| `host-chain.xml` | 75 passed |
| `scoped-integration.xml` | 64 passed, including three full-chain paraphrases and G refusal |
| `unit-regression.xml` | 670 passed / 2 environment failures |
| `unit-environment-fixed.xml` | 6 passed after creating the old tests' required `.tmp` parent; no test code changed |
| `http-product-regression.xml` | 157 passed / 8 failed; two in-scope failures subsequently corrected, six baseline failures below |
| `admission-reviewed.xml` | 66 passed after the HTTP assertion and refusal-code compatibility fixes |
| `canary-scope.xml` | 1 passed: context cannot widen the frozen LIVE Canary question or reserve a request |
| `review-p2-red.xml` | Independent-review write-admission reproduction: 10 failed / 13 passed before repair |
| `review-p2-green.xml` | 164 passed after the write-admission repair, including M3-L HTTP/Canary/source/budget and Fast Path regressions |
| `review-p2-red2.xml`, `review-p2-red3.xml` | Review counterexamples retained: 10/33 and 11/44 failures before their repairs |
| `review-p2-green3.xml` | 185 passed after operation grammar, polite-prefix and history-noun repairs |
| `review-p2-red4.xml` | 6 further completion/report counterexamples reproduced, 44 passed |
| `review-p2-green4.xml` | 191 passed, including 50 admission/Host tests and M3-L/HTTP/source/budget checks |
| `review-p2-red5.xml` | 5 compound-read refusals reproduced; 52 controls passed |
| `review-p2-green5.xml` | Final 198 passed, including 57 admission/Host tests and M3-L/HTTP/source/budget checks |
| `m3l-offline/summary.json` | C1/C2/C3 all PARTIAL, Reviewer accepted; mocked SDK requests 8/8/9 |
| DOM regression and browser check | Passed; domain change invalidation, exact context submission, bilingual scope clarification, original question retained, Fast Path 0 model calls |

Counts overlap and must not be summed as unique tests. Budget/source/adapter
unit coverage is included in the 672 related cases; source/version revocation,
HTTP, product main chain and task Runtime checks are included in the broad
integration attempt. Existing default budgets and all G premise gates were
retained. Browser evidence: `browser-scope.png`, `browser-fast-path.png`.

Retained baseline/environment evidence:

- Initial unchanged-code check: 84 passed / 4 M3-L failures with
  MODEL_ARTIFACT_WRITE_FAILED under long Windows test paths. Loading the frozen
  old router into the same tree with a shorter test root passed all 18 M3-L
  cases (`baseline-short.xml`). The new offline Canary also passed all cases.
- Two unit failures were missing `.tmp` parent, corrected by environment setup.
- Six broad integration failures were reproduced **unchanged** using the frozen
  old router (`baseline-known-failures.xml`): HOLD-G returned refused rather than
  partial; two legacy interrupted-provider cases hit TOOL_INVALID_ARGUMENTS
  instead of the expected 503; two tests require untracked missing
  `work/temporal_decision_eval/GOLD.json` and `CASES.json`; one legacy ingestion
  test expects TEMPORAL_UNRESOLVED but gets ENTITY_UNRESOLVED. None uses the new
  entry contract. Their implementation modules are unchanged from the base.
- Full unit/integration collection stopped with four old import errors:
  `run_memoryagentbench_g3_pilot.py` (three tests) and
  `run_stage_c_context_memory_loop.py` (one test) are absent from the baseline
  Git tree (`full-collection.xml`). No full-suite pass is claimed.
- The installed pytest-asyncio fixture-loop warning and pyreadline destructor
  WinError remain environment noise; pytest exit codes/XML are retained.
- Raw pytest failure XML retains traceback whitespace. The final source/docs
  diff whitespace check excludes those unmodified raw XML artifacts.

The new HTTP test initially asserted a two-call default; the existing G default
is three. The assertion was corrected, not the budget. The router also restored
the old deterministic UNSUPPORTED_QUERY refusal when adaptive planning is off;
this compatibility fix adds no execution or model call.

## Review, protection and next boundary

Independent read-only review completed before commit/push; its final verdict is
**reported in-scope P1/P2 closed**, with no general semantic/lexical completeness
claim. The findings, repairs and verification are recorded in `REVIEW.md`.
The reviewer found a P2: literal edit/modify/change-status/archive/mark-complete
requests bypassed the old read-only vocabulary. The new Host operation guard
rejects explicit commands/polite requests before F/G, with 27 write-request
tests and 23 read-only change-history/report controls. The frozen 48-case routing set
was not expanded or rewritten to hide the finding. This guard does not certify
arbitrary semantic goal alignment; all existing tools remain read-only.
`accepted-checks.json` confirms final source fingerprints, unchanged frozen cases,
empty protected diffs and per-XML counts. Earlier after-matrices remain historical
development evidence. Temporary execution material is retained under `work/`,
the old tests' ignored `.tmp/`, and `work/review-direct/`; nothing
was deleted as cleanup.
Protected source/manifest/evaluation diffs are checked against the baseline.
No F shared extractor, G operation, semantic authority, Runtime, frozen Canary
v1/v2, historical ledger/evaluation, real vault, PR or master was modified.

Smallest next step: separately approve a bounded quality evaluation of the
explicit-domain entry. Before automatic domain selection, define a Host/G
contract for domain-not-applicable/clarification without starting task execution;
the current F-before-G interface does not provide that guarantee. No new
classifier or parallel execution loop is justified by this offline route set.

## Reproduction

From this worktree, use fresh output/test directories (the route runner refuses
to overwrite an existing evidence file):

```powershell
$env:PYTHONPATH='src;.;tests/integration'
python scripts/evaluate_m3m_admission.py --output work/m3m-new.json --execution-root work/m3m-new
python -m pytest -p scripts.evaluation_offline_plugin tests/unit/test_ask_admission.py tests/integration/test_m3m_admission.py tests/unit/test_unified_ask.py tests/unit/test_scope_discovery.py -q --basetemp=work/m3m-tests
node tests/ui/unified_ask_dom_test.cjs
python scripts/run_m3l_offline_acceptance.py --output work/m3l-new
```

On this Windows checkout, the wider M3-L test suite needs a short test root to
avoid model-artifact path length failures. Tests using the old adaptive harness
also require the ignored `.tmp/` parent. Localhost fixture access may need the
execution environment's localhost exception; the offline plugin still denies
all external connections.
