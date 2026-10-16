# SPEC: Evaluation qualification boundary

Status: **APPROVED_FOR_PREPARATION — new Final execution requires user approval**

The human-approved boundary is evaluation infrastructure: read-only V3 failure
disposition, automatic preflight, a corrected observation-driven test runner,
an independent tracked-data Core Gate, a fresh full 249-node comparison, and
preparation of a new author-visible validation set. All implementation stays
in this worktree; production modules and historical frozen assets stay unchanged.

## Problem

The historical V3 set is frozen and must remain unchanged, but its recorded
runner qualification was incomplete (`dev 2/10 passed, 5 failed, 3 N/A;
final 0/10 passed, 8 failed, 2 N/A`). This qualification milestone must
re-run the complete 249-node comparison against baseline commit `37e45b1` and
candidate commit `499bb37`, record the actual results, and keep that evidence
separate from the new case set. A new qualification package must make input
identity, source evidence, partition separation, and final-execution status
explicit before a runner can claim readiness.

## User-visible result

Reviewers can inspect one static bundle with six development cases and ten
final cases. Every case has a distinct synthetic source graph, a typed query
target, author-visible Gold, and exact source spans for every Gold fact. A
static validator reports the case IDs, partitions, graph fingerprints,
evidence IDs, and safety gates. It does not call a Provider, execute a final
case, or claim model accuracy.

## Scope

This package contains:

1. `qualification-cases/v1` input records with exactly 16 cases: `6 dev` and
   `10 final`.
2. A companion validation manifest under
   `tests/fixtures/evaluation_qualification/validation/` containing stable
   case/family IDs, source-evidence IDs, normalized relation graphs, line
   spans, partition membership, and rename-leak checks.
3. `scripts/build_qualification_validation.py`, a small deterministic builder
   and static checker. It validates exact quotes and lines, graph diversity,
   split membership, the absence of Gold from model input, and the fact that
   final execution remains `NOT_RUN`.
4. A handoff contract for the main Agent's static preflight API and new
   runner-boundary gate. The main Agent owns those implementation files and
   their tests.
5. Full 249-node baseline/candidate comparison evidence for `37e45b1` versus
   `499bb37`. The run is local qualification evidence; it must not modify the
   frozen historical inputs or call a Provider.

The proposed case bundle shape is deliberately small:

```json
{
  "schema_version": "qualification-cases/v1",
  "cases": [
    {
      "id": "EQ-D01",
      "partition": "dev",
      "target": "normal",
      "input": {
        "query": "...",
        "workspace_id": "...",
        "documents": [{"path": "...", "content": "...", "workspace_id": "..."}],
        "task_plan": {"schema_version": "task-plan/v2", "goal": "...", "subgoals": []},
        "budget": {"max_steps": 8, "max_provider_requests": 4}
      },
      "protocol": {"runner": "qualified-runtime-v1", "fault": null},
      "gold": {
        "outcome": "complete",
        "facts": [{
          "subject": "...",
          "predicate": "owner_of_project",
          "object": "...",
          "supports": [{"path": "...", "line_start": 2,
                        "line_end": 2, "quote": "..."}]
        }]
      }
    }
  ]
}
```

`gold` is author-visible preparation data. The runner must keep it outside
model/provider input and may read it only after an offline execution result is
available. The companion manifest assigns each source span an opaque,
static-validation-only evidence ID; the runtime may materialize its own
evidence identity from the documents. No fixed index ID or pseudo source hash
is part of this bundle.

## Case families and diversity

The cases use the existing supported relation vocabulary only:
`alias_of_project`, `owner_of_project`, and `reports_to`. Source prose uses the
existing owner, direct-report, Chinese owner/leader, and alias forms accepted
by the genuine validator. The 16 graphs vary topology and source incidence;
they are not renamed copies of one template.

Required families include complete two-hop answers, owner-only partials,
competing current owners, alias-to-owner paths, Chinese boundary cases,
malformed tool arguments, invalid Final correction, cold resume, Provider
uncertainty, workspace isolation, source freshness, and budget exhaustion.
The validator requires every case to have a unique normalized graph plus
document/paragraph incidence signature across both partitions. A rename-only
copy is rejected.

## Important non-goals

- No changes under `src/` or to any production module.
- No edits to the historical `real_agent_contract_v3` Gold, freeze, runner, or
  other old-frozen artifacts.
- No Provider, network, Gemini, or live model request.
- No execution of any new `final` case; final status stays `NOT_RUN`.
- No implementation of a second Agent framework, retrieval engine, or generic
  benchmark harness.
- No blind-test or generalization claim. Gold is author-visible and supports
  preparation and contract checks only.
- No claim of improvement before the complete 249-node rerun produces actual
  comparable results. The rerun's raw counts and failures remain separate
  from the new-case static preparation report.

## Ownership and boundary

Workers have separate file ownership for static data, full differential
regression, and Runtime test infrastructure. The main Agent integrates the
preflight and evidence. An independent Reviewer checks the final boundary.

## Acceptance criteria

1. Scope, evaluation protocol and exact source-backed Gold are defined before freeze preparation.
2. The bundle has exactly 16 unique case IDs: 6 `dev` and 10 `final`; every
   case has one allowed target and a valid budget.
3. Every Gold fact has a supported predicate, an exact quote, and inclusive
   line spans that resolve to the same source document text.
4. The companion manifest gives each fact/source span a unique evidence ID and
   records its normalized source relation, family, and partition.
5. All cases are synthetic and inline; no case contains a fixed index ID,
   provider result, scripted Final answer, or Gold-derived model instruction.
6. Model-input projection contains no `gold`, expected outcome, evidence IDs,
   expected facts, or validation-only metadata.
7. Normalized graph and source-incidence signatures are distinct across all 16
   cases; the validator rejects a mechanical rename collision.
8. Static checks reuse production validators but never execute a model or a
   new Final. Old exposed artifacts may be read for regression/leak detection.
   The Core Gate actually observes all ten requested Runtime boundaries;
   skipped, empty, or missing-input checks cannot become passes. It reproduces
   in a clean tracked-file checkout with an explicit dependency environment.
9. The preflight/runner handoff states `final_execution: NOT_RUN` and
   `real_provider_calls: 0`; later execution requires explicit user approval.
10. The complete 249-node baseline/candidate comparison runs for `37e45b1`
    and `499bb37`, with raw pass/fail counts, changed-file identity, and
    failures recorded without editing old frozen artifacts.
11. Independent review can mark the package `READY_FOR_APPROVAL` with an
    explicit changed-file list, static check output, and unresolved-risk note.

## Evidence and review

The builder's report is preparation evidence, not an evaluation score. Review
must separately inspect the 249-node historical comparison, the new bundle's
static checks, and the main Agent's preflight/runner implementation. The new Final remains unexecuted until the user approves a later experiment.
