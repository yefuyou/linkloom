# Implementation Plan: Agent Trajectory Evaluation Consumer

Status: Human-approved boundary; implementation authorized on 2026-08-23/24. Planner does not self-approve.

## Boundary

Implementation is limited to `src/linkloom/evaluation/trajectory/`, the six synthetic fixture files under `tests/fixtures/trajectory_kb_v1/n/`, `tests/eval/trajectory_eval_v1/cases.jsonl`, and new trajectory tests only. Existing production runtime modules, existing tests, and `tests/eval/relation_gold.yaml` are protected and must remain byte-identical.

## Work packages

1. Contracts/parser: versioned JSON-safe case, observation, result, artifact and rubric parsing; canonicalize away timestamps/run IDs.
2. Fixture/cases: add exactly 41 JSONL cases across the exact 15 categories.
3. Capability matrix: consume per-case required capabilities and derive 23 executable and 18 future CASE counts; never hardcode runtime counts.
4. Deterministic grader: check route, tool id, canonical args, sequence, executor suppression, budget, ToolResult status/error, `NOT_FOUND`, ledger transitions, repeated call, observed loop condition, premature termination, pending ambiguity, evidence reference validity, and denied resource access.
5. Artifacts/metrics: emit the six exact artifacts and nine required metrics; future values are `UNSUPPORTED`/`NOT_EVALUATED`.
6. Tests/isolation: add schema, parser, grader, integration, gating, and isolation tests; assert no real vault, Gold, network, provider, or judge access.

## Reviewer remediation amendment

The independent Reviewer findings are within the already approved evaluator boundary and are implemented test-first in this order:

1. **RED — metric isolation.** Add tests that corrupt a non-mapped assertion on a multi-assertion executable case and prove the target metric is unchanged; corrupt a mapped assertion and prove only explicitly mapped metrics change. Then add a fixed metric-to-assertion registry and stop using aggregate case status as a numerator.
2. **RED — closed nested contracts.** Add table-driven invalid cases for every case/observation/result sub-schema: unknown and missing fields, wrong types/enums, wrong fault-union branch fields, invalid ToolResult status/error combinations, invalid termination variants, unregistered attribution pairs, assertions, and events. Then implement shared registries and exact-field/conditional validation. The grader and parser consume the same assertion registry so they cannot drift.
3. **RED — evidence anchors.** Add positive same-file heading and negative bare, empty-anchor, unknown-anchor, cross-file-anchor, traversal, and heading-less cases. Then validate exposed paths and stable heading slugs from the synthetic fixture only.
4. **RED — observable attribution.** Add tests showing that mutating `expected_attribution` does not affect the harness observation, mutating observable execution signals does affect it, and a mismatch fails a deterministic `attribution` assertion. Then implement one canonical attribution taxonomy and priority rule shared by schema, harness, and grader.
5. **GREEN and regression.** Preserve exactly 41 cases, 15 categories, 23 executable cases, and 18 future cases; regenerate the six artifacts; run focused tests followed by all required checks; independently re-review the result.

The Worker may update only the approved trajectory evaluator, its golden JSONL, synthetic fixture validation, generated artifacts, and new trajectory tests. It may not weaken an assertion to make a golden case pass.

## Required checks

```powershell
python -m pytest -q tests
python -m compileall -q src/linkloom tests
git diff --check
```

## Forbidden changes

No optional judge adapter implementation, real judge/provider, production runtime changes, LangGraph, retries, replay, memory redesign, real vault, Gold mutation, commit, push, or PR.

## Handoff

Worker implements only this boundary. Reviewer independently verifies the SPEC, exact counts/categories/artifacts/metrics, test results, and byte-identical protected files. Planner does not accept the result.
