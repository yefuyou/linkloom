# SPEC: Agent Trajectory Evaluation Consumer

Status: Human-approved boundary; implementation authorized on 2026-08-23/24. Planner does not self-approve.

## Purpose

An independent, read-only consumer for evaluating Agent trajectories on synthetic fixtures. It is evaluation behavior, not production runtime behavior.

## Scope

Exact categories: `routing`, `tool_selection`, `malformed_arguments`, `permission_denied`, `budget_exhausted`, `tool_execution_failure`, `invalid_output`, `NOT_FOUND`, `partial_retrieval_failure`, `repeated_tool_call`, `loop`, `premature_termination`, `checkpoint_pending_ambiguity`, `stale_memory`, `conflicting_memory`.

Cases live in `tests/eval/trajectory_eval_v1/cases.jsonl` and use only `tests/fixtures/trajectory_kb_v1/n/{agent-loop.md,retrieval.md,checkpoint.md,memory-policy.md,empty.md,missing.md}`. There are exactly 41 cases covering all 15 categories; tests derive and verify this count.

## Case contract

Every case contains: `case_id`, `category`, `synthetic_input`, `policy/budget`, `fault_injection`, `expected trajectory`, `expected ToolCall(s)`, `expected ToolResult(s)`, `expected termination`, `expected attribution`, `deterministic assertions`, and optional `judge rubric`.

Contracts are versioned and JSON-safe. Canonical deterministic comparison excludes timestamps and run IDs; they are metadata only.

### Strict nested schemas

`trajectory-case/v1`, `trajectory-observation/v1`, and `trajectory-result/v1` are closed contracts. Every object at every nesting level rejects missing fields, unknown fields, wrong JSON types, unknown enum values, and invalid conditional combinations.

- A case has exactly the registered top-level fields. `synthetic_input` has exactly `request` and `fixture_paths`; `policy_budget` has exactly `allowed_tools`, `denied_tools`, and non-negative integer `max_tool_calls`.
- `fault_injection` is a discriminated union on `kind`. Each registered kind has one exact required/allowed field set; unknown kinds, fields from another branch, and incomplete branches are invalid.
- A ToolCall has exactly `call_id`, `tool_id`, `arguments`, and non-negative integer `sequence`.
- A ToolResult has exactly `call_id`, `tool_id`, `status`, `value`, `business_status`, and `error`. For `status=ok`, `error` is null and `business_status` is `FOUND` or `NOT_FOUND`. For `status=error`, `value` and `business_status` are null and `error` has exactly `code`, `category`, `message`, `retryable`, and `safe_to_expose` with their registered types/enums.
- Termination is a discriminated union on the exact status enum: `budget_exhausted`, `completed`, `durability_uncertain`, `needs_clarification`, `partial`, `recovery_decision`, `refused`, or `safe_error`. Each status permits only its registered conditional field set.
- Attribution has exactly `primary` and `stage`; only registered primary/stage pairs in the single canonical taxonomy are valid.
- Every deterministic assertion has exactly `assertion` and `expected`; the assertion name must exist in the deterministic-handler registry. Every trajectory event must exist in the event schema registry and match that event's exact fields.
- The optional judge rubric has exactly `status`, `provider`, and `criteria`; v1 requires `NOT_EVALUATED`, null provider, and non-empty textual criteria. No provider is called.
- Observation reuses the same registered event, ToolCall, ToolResult, termination, and attribution schemas. Its `execution` subobjects and result assertion records are closed contracts. Result status conditionals for `PASS`, `FAIL`, and `NOT_IMPLEMENTED` are enforced, including missing-capability and failure fields.

The registries are executable schema sources of truth and must cover all existing golden values. Adding a new fault, event, assertion, termination, or attribution pair therefore requires an explicit registry and test change.

## Exact artifacts

Each run produces exactly: `run_manifest.json`, `case_results.jsonl`, `metrics.json`, `failure_summary.json`, `capability_matrix.json`, and `report.md`.

## Capability gating

A central capability matrix derives case-level support from each case's required capabilities. It must report exactly 23 executable cases and 18 future cases for v1. These are CASE counts, not capability counts, and must never be hardcoded as runtime values. Every future case result has status exactly `NOT_IMPLEMENTED` and a non-empty `missing_capability`; it is not scored as an ordinary failure. `UNSUPPORTED` and `NOT_EVALUATED` apply only to future-related metrics and optional judge results.

Every case result has status exactly `PASS`, `FAIL`, or `NOT_IMPLEMENTED` and includes `failed_assertion`, `expected`, `actual`, and `primary_failure_attribution`.

## Deterministic grader

The grader explicitly supports route, tool id, canonical args, sequence, executor suppression, budget, ToolResult status/error, `NOT_FOUND`, ledger transitions, repeated call, loop condition if observed, premature termination, pending ambiguity, evidence ref validity, and denied resource access.

No judge/provider is executed. An optional `judge rubric` is data only; its result is `NOT_EVALUATED`.

Evidence references are valid only in the form `<exposed-fixture-path>#<non-empty-anchor>`. The path must be one of the six exposed synthetic fixture files, and the anchor must be the stable slug of a heading that actually exists in that exact file. Bare paths, empty anchors, unknown anchors, cross-file anchors, and traversal are invalid.

For every executable case, observed attribution is derived only from observable execution signals such as ToolResults, ledger transitions, budget decisions, denials, recovery decisions, and registered trajectory events. The harness must not read or copy `expected_attribution` while producing the observation. A registered deterministic `attribution` assertion compares the derived `{primary, stage}` pair with the expected pair.

## Required metrics

Executable cases only contribute to: `argument_contract_accuracy`, `permission_block_rate`, `budget_accounting_accuracy`, `executor_suppression_rate`, `tool_result_contract_accuracy`, `not_found_semantic_accuracy`, `partial_retrieval_recovery_rate`, `pending_ambiguity_safety_rate`, and `safe_error_exposure_rate`. Future-related metric statuses are `UNSUPPORTED` or `NOT_EVALUATED`; these statuses never apply to case results.

Each metric has one explicit registry entry naming its eligible cases and contributing deterministic assertion subset. A metric is calculated from those assertion results, never from the case's aggregate `PASS`/`FAIL` status. A failure in an assertion outside a metric's registered subset must not alter that metric; a failure inside the subset may alter only metrics that explicitly include it.

## Acceptance

- [ ] 41 cases and all exact 15 categories validate.
- [ ] Versioned JSON-safe contracts reject invalid input and preserve canonical determinism.
- [ ] Capability matrix derives 23 executable and 18 future case counts.
- [ ] Every case result is `PASS`, `FAIL`, or `NOT_IMPLEMENTED` and includes `failed_assertion`, `expected`, `actual`, and `primary_failure_attribution`.
- [ ] Every future case result is `NOT_IMPLEMENTED` with a non-empty `missing_capability`.
- [ ] Six artifacts are generated and parseable.
- [ ] Nine metrics are implemented only for executable cases.
- [ ] Cross-contamination tests prove an unrelated assertion failure leaves a metric unchanged and a mapped assertion failure changes only the registered metric(s).
- [ ] Table-driven schema tests reject unknown, missing, wrong-type, wrong-enum, invalid-union, and invalid-conditional values at every registered nested contract.
- [ ] Evidence tests accept a real same-file heading anchor and reject bare, empty, unknown, cross-file, traversal, and heading-less references.
- [ ] Attribution tests prove expected-attribution mutation cannot change the observed attribution, observable-signal mutation can, and mismatches deterministically fail grading.
- [ ] Schema, isolation, parser, grader, integration, and gating tests exist.
- [ ] `python -m pytest -q tests` passes.
- [ ] `python -m compileall -q src/linkloom tests` passes.
- [ ] `git diff --check` passes.
- [ ] Protected production runtime modules, production tests, and `tests/eval/relation_gold.yaml` remain byte-identical.
- [ ] No real vault, Gold, network, provider, or judge is accessed.

## Non-goals

No production runtime rewrite, LangGraph, retries, replay, memory redesign, real vault, network/provider/judge, commit, push, or PR.
