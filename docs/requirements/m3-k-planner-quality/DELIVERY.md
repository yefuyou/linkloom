# M3-K delivery

Base: `167a46694048e9ae6dd26879c2c6986fed8c0a15`, independently read back
from `origin/codex/linkloom-m3-question-planning`. New branch:
`codex/linkloom-m3-k-planner-quality`. All implementation edits are confined to
`D:/webproject/Linkloom/work/linkloom-m3-k-planner-quality`.

## Implemented

The existing LLM planner still owns semantic decomposition. Host now binds each
step to workspace, exact evidence spans/versions, inclusive date scope and typed
dependency premises, including verified-result digests/outcomes. Question prose,
step names and handle aliases do not create fresh operations. Different unknown
entities do not collapse into the same endpoint-free discovery.

Discovery requires a goal-related evidence_gap. Repeated Inspection, Discovery
or Relation work requires a concrete new gap when scope/premises change; exact
duplicate/completed work is rejected even with a textual excuse. Public
diagnostics expose proposed counts/by-kind, accepted/ready counts, all excessive
steps in structurally valid responses, and per-step violations. Structural errors
are labeled separately; a zero counter there is not proof that the whole plan
was semantically efficient. Gap text remains an unverified model justification.

Inspection still requires one authorized ObservationRef, a supported field and
empty unrelated operation fields. Its explicit sources must include that exact
task provenance. The provider-compatible schema and generated capability
inventory clarify this contract; validator checks remain strict. Host may pass
`supported_operations=frozenset(...)` to restrict the exposed capability enum.

`plan.ready_steps` and `plan.semantic_queries` exclude completed work and work
whose dependencies lack Host-verified receipts. Relation premises require
SUPPORTED; COMPLETED/UNKNOWN/REFUTED/CONFLICT do not unlock dependents. Replan
rejects attempts to mark work complete without positive prerequisites and checks
the original completed evidence bindings, including handle rebinding. Exact goal,
workspace, lineage and cumulative call/token reservations remain enforced.
Implicit full-source receipts also become invalid when inventory grows. Callers
that need a discovery receipt to survive newly admitted sources must plan an
explicit evidence scope; that discovery-to-new-handle replan is covered by a
positive integration-contract test.

Time-scoped or explicit-source relations cannot be converted into a SemanticQuery
that silently drops their bounds: the Host must bind the complete ready step
through its existing tools. Endpoint-only projections remain available for ready
unfiltered relations. No executor/coordinator/runtime is added or registered.
The plan schema/id and emitter version advance to v2; historical artifacts remain
v1. Additional per-step metadata is optional except where the gap rule requires it.

## Measured evidence

| Population | Category coverage | Valid plans | Excess work | Deduplication |
|---|---|---|---|---|
| Original three Gemini calls, unchanged | 6/7 (85.7%) | 2/3 (66.7%) | Manual owner audit: 1 unjustified supplier discovery; 1 newer-task discovery potentially necessary | Old scores unchanged |
| Saved accepted Gemini outputs, offline revalidation | Not a new quality score | 1/2 replayable accepted outputs; third rejected arguments unavailable | Owner has 2 unexplained Discovery steps under the new contract | No new prediction |
| New authored FakeModel responses | 20/20 | 11/11 non-refusal plans, plus 1 valid refusal | 0 | Contract evidence only |
| Nine dedicated typed identity probes | N/A | N/A | N/A | 0 false dedup, 0 missed dedup, 0 probe failures |

Machine-readable results and full authored plans:
[metrics-reviewed.json](evidence/metrics-reviewed.json). Four budget-admission probes
pass: input ceiling, cumulative input ceiling, cumulative output ceiling, and
charging invalid output against the call ceiling without retry. New paid calls:
**0**; new paid provider cost: **$0**; no model request allowance increased.
Original live use remains 3 calls and 18,055 tokens. Fake usage is not provider
usage/cost. No real-model planning improvement is claimed.

[DIAGNOSIS.md](DIAGNOSIS.md) records the exact available evidence and uncertainty.
The original INVALID_INSPECTION branch is narrowed, but its precise offending
field cannot be recovered because G did not retain rejected arguments. M3-K's
new diagnostics make future malformed outputs attributable without weakening
validation. No fabricated replay is presented as the lost model response.

## Checks and limits

- Planner after independent-review repairs: **79 passed**,
  [review-repairs.xml](evidence/review-repairs.xml).
- Related source/relation/query/store/context tests: **157 passed**,
  [related-complete.xml](evidence/related-complete.xml).
- Python compilation and Git whitespace checks: passed.
- Full unit/integration offline collection: **4 pre-existing missing-script
  import errors**, matching G's recorded baseline; [offline.txt](evidence/offline.txt).
  Full suite is not green and no broader green claim is made.
- Extra unchanged Gemini schema tests: 3 passed, 1 failed. Exporting baseline
  `167a466` source/test files into this Worktree reproduces the identical
  `client.requests[0]` IndexError: [baseline-p85.txt](evidence/baseline-p85.txt).
  This adapter/test defect is outside M3-K and was not changed.
- Initial related-test attempts hit a non-writable system temp directory and
  then a missing local temp parent. Creating the parent and using a new private
  Worktree basetemp resolved those environment errors; final 157 tests pass.

Independent review and any required repairs are recorded in [REVIEW.md](REVIEW.md)
before commit/push. No real vault, F/E source files, historical Gold, frozen data,
old evidence/scores, other existing Worktrees, PR, merge or deployment is involved.

## Reproduce without a provider

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONIOENCODING='utf-8'
python -m pytest tests/unit/test_investigation_planner_quality.py tests/unit/test_task_relation_question_planner.py -q
python scripts/evaluate_planner_quality.py --output work/m3k-replay-new.json
```

The evaluator writes exclusively to a new output file and has no live mode. It
reads old live predictions only for separate historical analysis and never edits
them. Temporary baseline exports and pytest materials are retained under work/;
no cleanup deletion was authorized. An app-generated unused detached checkout
was also preserved after Worktree registration stalled; implementation used the
explicit Git Worktree above, and no files there were edited.

The logical next step is Host integration of full ready-step capabilities and
receipts, then a separately authorized real-model quality evaluation if desired.
Neither is part of this delivery.
