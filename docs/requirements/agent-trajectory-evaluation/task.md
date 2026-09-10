# Task: Agent Trajectory Evaluation Consumer

Status: Feature implemented and independently accepted. Repository-wide test gate remains red only because legacy relation-eval tests reference a missing external path; this is recorded below and is not represented as a full-suite pass.

## Acceptance checklist

- [x] Add only `src/linkloom/evaluation/trajectory/` implementation files and new trajectory tests/fixtures.
- [x] Add `tests/fixtures/trajectory_kb_v1/n/{agent-loop.md,retrieval.md,checkpoint.md,memory-policy.md,empty.md,missing.md}`.
- [x] Add `tests/eval/trajectory_eval_v1/cases.jsonl` with exactly 41 cases.
- [x] Cover exact 15 categories from the SPEC.
- [x] Validate all exact case fields and optional judge rubric.
- [x] Ignore timestamps and run IDs in deterministic grading.
- [x] Derive 23 executable and 18 future CASE counts from required capabilities and the central capability matrix.
- [x] Return every future case with status exactly `NOT_IMPLEMENTED` and a non-empty `missing_capability`.
- [x] Restrict `UNSUPPORTED`/`NOT_EVALUATED` to future-related metrics and optional judge results.
- [x] Make every case result status exactly `PASS`, `FAIL`, or `NOT_IMPLEMENTED` and include `failed_assertion`, `expected`, `actual`, and `primary_failure_attribution`.
- [x] Implement the complete deterministic grader support list from the SPEC.
- [x] Emit `run_manifest.json`, `case_results.jsonl`, `metrics.json`, `failure_summary.json`, `capability_matrix.json`, `report.md`.
- [x] Implement the nine exact metrics; future metrics are unsupported/not evaluated.
- [x] Add schema, isolation, parser, grader, integration, and gating tests.
- [x] Do not implement a judge adapter; rubric data only and `NOT_EVALUATED` behavior.
- [x] Run `python -m pytest -q tests`; latest result is `458 passed, 2 skipped, 8 failed, 7 errors`. All 15 nonpasses are legacy relation-eval tests hard-coding the missing `D:\webproject\vault-steward` path, so this is not a full-suite pass.
- [x] Run `python -m compileall -q src/linkloom tests`; exit 0, with one warning for an inaccessible stale pytest temp directory.
- [x] Run `git diff --check`; exit 0, with pre-existing line-ending warnings.
- [ ] Verify every protected production runtime module and existing test is byte-identical. The recorded protected hashes match for ToolRuntime, Coordinator, RetrievalAgent, RuntimeAdapter, Runtime graph, and relation Gold; `runtime/model_loop.py` and existing tests have concurrent changes outside this task, so repository-wide byte identity cannot be claimed.
- [x] Verify `tests/eval/relation_gold.yaml` is byte-identical (`24087aa72a2ec053363c59d3d1fdbfe193694f456f75998fe14f28f9fcf1ccf8`).
- [x] Verify no real vault, Gold, network, provider, or judge was accessed.
- [x] Independent Reviewer records feature `PASS` with no open P0-P3 finding.

## Required evidence

## Completed work

- Added a versioned, JSON-safe, closed trajectory case/observation/result contract, including registered fault, event, assertion, termination, attribution, tool, and metric schemas.
- Added six synthetic fixture notes and exactly 41 JSONL golden cases across the 15 required failure categories.
- Added a read-only harness and deterministic grader that consume current ToolRuntime contracts without changing production behavior.
- Added capability gating, exact six run artifacts, nine independent assertion-level metrics, real heading-anchor validation, and observable-signal failure attribution.
- Added schema, fixture isolation, parser, grader, harness, metric, reporting, capability, and integration tests.

## Test results and evidence

- Coordinator trajectory suite: `124 passed in 4.28s`.
- Independent Reviewer focused suite: `120 passed in 3.82s` plus adversarial schema and attribution probes.
- Evaluation CLI: `23 PASS / 0 FAIL / 18 NOT_IMPLEMENTED`.
- Dataset/artifact audit: 41 unique cases, 15 categories, six fixtures, six artifacts, and nine metrics at `1.0` for the current executable subset.
- Exact full repository suite: `458 passed, 2 skipped, 8 failed, 7 errors`; all nonpasses are the pre-existing absolute-path relation-eval group and do not import trajectory evaluation.
- `compileall` and `git diff --check`: exit 0.
- Integration guards block network and Gold reads, assert no provider/judge calls, and verify synthetic fixtures plus relation Gold are unchanged.
- Independent Reviewer verdict: feature `PASS`; correctness, readability, architecture, security, and performance all pass.

## Learning points

1. A capability metric must consume only its registered deterministic assertions, not aggregate case status, or unrelated failures contaminate multiple scores.
2. Fault injection may create a scenario, but attribution must be derived from observable ToolResults, ledger/recovery state, termination, and trajectory events rather than the hidden fault label.

## Interview evidence

- Runnable offline CLI and six parseable artifacts.
- A 41-case failure taxonomy with 23 executable and 18 explicitly gated future cases.
- RED-to-GREEN reviewer probes for metric isolation, nested schema closure, heading anchors, and observable attribution.
- A traceable `NOT_FOUND` business-status example, permission/budget executor-suppression examples, partial-retrieval evidence preservation, and pending-call recovery classification.

## Remaining issues

- The repository-wide suite is not green until the legacy `D:\webproject\vault-steward` test paths are repaired or formally retired.
- `runtime/model_loop.py` and other dirty-worktree files changed concurrently outside this task; they must not be attributed to trajectory evaluation.
- The 18 future cases remain `NOT_IMPLEMENTED`: model routing/tool selection/semantic guards, full resume, memory freshness/conflict, and LLM judge.
- The evaluator-local runtime import bootstrap remains a documented host-state coupling risk; production code was not changed to remove it.

## Candidate next task

Repair or formally quarantine the six legacy root relation-eval test files that hard-code `D:\webproject\vault-steward`, then rerun the exact full repository suite. This is a separate boundary and was not performed here.

## Forbidden scope

Production runtime rewrite, LangGraph, retries, replay, memory redesign, real vault, Gold changes, network/provider/judge use, commit, push, and PR.
