# M3-F independent read-only review

Verdict: **PASS with notes**. Initial **REQUEST CHANGES (P1)** is closed.

Reviewer: independent subagent `/root/reviewer`, `gpt-5.6-luna`, reasoning `max`.
The Reviewer inspected files and used `python -B` in-memory FakeModel probes;
it did not edit files, alter Git state, run pytest or call a real Provider.
This document records its returned findings and final verdict; the Worker applied fixes.

## Scope and evidence

Reviewed the approved SPEC/implementation plan, new M1 discovery service, its
unit tests, public source/label fixtures, standalone diagnostic/scorer, DELIVERY,
live responses, replay outputs and final test evidence. Checked the real existing
ProviderNeutralSemanticExtractor, SemanticExtractionResult, TaskProposal,
TaskObservation, M1 validator and SourceReferenceRegistry contracts.

## Substantive finding and resolution

P1: the original veto admitted common non-task wording when FakeModel incorrectly
classified it as PLAN. The nine independent counterexamples were discussion,
unadopted work, possibility, proposal, hypothesis, indirect reporting, unnecessary
work and task termination (including “今天仅讨论完成索引测试”).

Worker first added these as tests: `evidence/reviewer-red.xml` records 9 failures
and 6 passes. Worker expanded only the refusal grammar, preserving model-owned
positive discovery and unchanged M1 field validation. Final Reviewer independently
reproduced all **15** parameterized negative examples: each yielded **0 valid /
0 partial**, with NON_ASSERTED_TASK. It also inspected the conservative
cross-message referential cancellation rejection.

Final Reviewer: **“P1 已关闭；PASS with notes。”** No substantive findings remain open.

## Acceptance matrix

| Criterion | Evidence | Result |
|---|---|---|
| Existing extractor/tool/schema reused; separate bounded M1 service | source and structured FakeModel tests | PASS |
| Zero/one/multiple tasks, same-owner/day distinct tasks, cross-message partial projection | task_observation_discovery tests | PASS |
| Field grounding, missing owner/year, first person, mixed correction | existing M1 + new tests | PASS |
| Suggestions/hypotheses/quotations/negation/cancellation rejected | 15 independent negative probes + referential cancellation tests | PASS within declared conservative grammar |
| Current versions, exact evidence, revocation, cross-workspace, partial parse, duplicate/oversized input and budget | new tests, final regression | PASS |
| Explicit valid/partial/failed/unprocessed and non-exhaustive coverage | typed result and tests | PASS |
| Relevant regressions | accepted-regression.xml: **385 passed, 0 failures/errors** | PASS |
| Separate scorer, including false merge and NOT_RUN | scoring-final.xml: **3 passed** | PASS |
| <=3 live calls, new public inputs, no Gold/endpoints supplied | live requests/manifest/usage | PASS: 3 calls, no retry |
| Independent discovery metrics and honest missed work | reviewed-metrics.json | PASS: strict 4/5 match; semantic limitation retained |
| Final replay preserves original structured outputs | reviewed-replay versus live, three cases | PASS; zero network calls |
| New files only; no protected subsystem/old eval changes | Git state and dependency hash guards | PASS |

## Notes and limits retained

1. Pre-fix RED and earlier focused outputs are historical evidence. Final regression
   evidence is `accepted-regression.xml`, not the earlier 28-test focused file.
2. Real Recall/Precision are **0.8/0.8** under strict complete-work matching.
   Matched fields are **24/24**, exact observation evidence **5/5**. The model
   omitted cross-message “补齐回归脚本”; no semantic-exhaustiveness claim is accepted.
3. Live manifest captures the implementation/runner at generation time. Refusal
   and scorer fixes subsequently changed their hashes. Current reviewed replay
   preserves all saved request results, but is not fresh live quality evidence.
4. Veto grammar may conservatively reject boundary wording and cannot prove
   general semantic correctness. Unresolvable referential cancellation withholds
   the current context rather than resolving business task identity.
5. Full unit/integration collection has four pre-existing missing-script errors;
   old Provider tests also have a model-ID mismatch. These are recorded in DELIVERY
   and checks.json and were not repaired or claimed as passing.
6. No real vault, deployment, CI or UI validation occurred. No runtime integration,
   formal decision authority or P1–P4 mutation was introduced.

## Architecture checkpoint

Responsibilities remain separated: Host owns permission/current-source loading;
existing Provider extractor owns semantic proposals; discovery schedules bounded
checks and projects original spans; M1 owns field validation. Groups remain
MODEL_PROPOSED; observations remain SUPPORTING_ONLY/UNCONFIRMED. No new database,
dependency, task-identity resolver or alternate LLM extraction stack was added.

Learning reflection: exact evidence can validate every emitted field while an
important task continuation is still missing. Quality evidence must retain recall,
the declared coverage policy and failure records alongside field/evidence accuracy.
