# M3-G delivery

Baseline: `31dde91f0e9b6015abc5754e995692a2f1db89ec`. Branch:
`codex/linkloom-m3-question-planning`. Implementation is confined to a dedicated
Git Worktree under `work/linkloom-m3-question-planning`.

## Implemented boundary

Standalone `InvestigationPlanner.plan(SemanticContext, discovery=...)` performs
one guarded model turn. It returns an immutable InvestigationPlan or an explicit
blocked/invalid/provider-failed/refused result. It never executes the suggested
work. There is no runtime registration or change to extraction, Runtime, Context,
UnifiedAsk, Coordinator, task_plan.py, P4, historical Gold or existing scorers.

The model chooses inspect/discover/relation/compare_dates/clarify questions and
earlier-step dependencies. Host binds only provided ObservationRef, ProjectRef,
ChangeRef and source handles. Relation steps expose `to_semantic_query()` and
the plan exposes pending `semantic_queries`. These are suggestions; the caller
must schedule dependencies and verify evidence before publishing any conclusion.

Missing entities produce discovery requirements, not fabricated endpoints. New
discoveries require Host verification and a fresh explicit replan. M2 hints never
restrict the available endpoint set; missing candidates prove no absence.
Semantic understanding comes from the model, with no new keyword classifier.

Integration sketch (composition only; nothing is registered or executed):

```python
from linkloom.semantic_ingestion.task_relation_question_planner import InvestigationPlanner

planner = InvestigationPlanner(
    model_adapter, model_id=model_id, request_guard=exact_request_budget_guard,
    guard_id=guard_id, result_verifier=current_receipt_verifier,
)
result = planner.plan(authorized_snapshot_with_empty_queries, discovery=m2_result)
if result.status == 'PLANNED':
    suggested_queries = result.plan.semantic_queries
    # Existing Host schedules dependencies, gathers evidence and invokes Reviewer.
```

Replan: pass `previous`, new `verified_results: tuple[VerifiedStepResult, ...]`
and explicit `gaps`. Supply `result_verifier(receipt, current_context) -> bool`;
it must revalidate the exact receipt/digest and current evidence. Host retains
all earlier receipts and completed steps. A receipt from the model is rejected.
Original question/workspace and latest-plan lineage cannot change in a session.
The verifier is an integration seam, not a built-in Reviewer or a grant of any
approval/write/database permission.

Default ceilings: 12 subgoals, 8 relation queries, 4 children/neighbors/handles,
6 dependency levels, 3 relation hops, 24,576 conservative input tokens and
4,096 output tokens per call. A session reserves at most 3 calls, 73,728 input
tokens and 12,288 output tokens, including failures. Input admission counts
UTF-8 bytes of the entire request plus framing as a conservative token bound;
unknown provider usage remains unknown. No automatic retry.

Each planner session owns a unique run ID that remains stable during replanning;
turn identities advance with attempted calls. Guard input is a deep copy and
both the authorization view and original request hash are checked before sending.
Known over-budget usage exhausts the session even on Provider error responses.

Typed execution signatures suppress paraphrases of the same operation/field/
endpoints. Discovery for one kind/anchor set is grouped into one step. This
does not establish semantic equivalence between different anchor sets or verify
business task identity; SAME_TASK still requires evidence and review.

## Evidence and actual limitations

[EXAMPLES.md](EXAMPLES.md) provides all 12 natural-language questions alongside
scripted outputs and the three actual live outputs. Machine-readable requests,
outputs, usage and metrics are under [evidence/](evidence/).

FakeModel replay: 12 questions, 20/20 required work categories covered, zero
accepted wrong endpoints or duplicate work, zero missing decompositions. These
are authored contract scripts, **not measured LLM semantic quality**.

Actual Gemini smoke: exactly 3 generation calls, no retries. Two plans accepted;
one rejected with `INVALID_INSPECTION`. Category coverage: 6/7 (85.7%); accepted
endpoint bindings: 16, wrong workspace/unknown bindings: 0; duplicates: 0;
one question has no accepted decomposition. Input/output/total provider tokens:
16,174 / 1,881 / 18,055. The estimate reserved by the guard was $0.25 per call;
this is an admission estimate, not a measured price or invoice.

Endpoint error counts mean invalid typed/Workspace/authorized-handle bindings;
they do not measure whether every selected endpoint is semantically relevant.
The rejected inspection is separately counted as an invalid endpoint output.

The live missing-change case did not meet the plan contract. It was rejected
without executing work, manufacturing endpoints or retrying. Its response hash,
usage and error are saved, but the initial runner did not retain its rejected
arguments. Subsequent implementation retains bounded public emitter arguments
for future diagnostics; no further live calls were made. Live provenance records
the exact pre-repair implementation hashes. Final-code behavior is verified
offline; live acceptance of the repaired code is not claimed.

The simple owner smoke also proposed extra discovery work because additional
source text described unavailable typed entities. Category coverage does not
measure such excess planning, endpoint relevance or generalization. The live
compound case asked relation questions rather than emitting a date delta.

## Reproduction

From this Worktree, in PowerShell:

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONIOENCODING='utf-8'
python -m pytest tests/unit/test_task_relation_question_planner.py -q
python scripts/run_question_planning.py --mode fake --output work/question-replay
python scripts/score_question_planning.py work/question-replay/results.json --output work/question-replay/metrics.json
```

The scorer runs separately after predictions exist. Planner and live generator
never import expected categories, Gold, case IDs or filenames to choose plans.
Exact requests contain the natural question, admitted source snapshot and limits.
The live runner uses the existing Gemini SDK/client adapter with transport
attempts=1, fixed three question ordinals, exclusive attempt files, bounded
tokens and no fallback. The three-call allocation for this delivery is exhausted;
the reproduction commands above deliberately use only FakeModel.

## Acceptance and integration

Independent review: **PASS with notes** after three Required findings were
repaired and independently closed; [REVIEW.md](REVIEW.md). Current Planner tests:
**40 passed** ([XML](evidence/review-repairs.xml)). The earlier related combination
passed 102 tests, including 66 existing semantic/context/runtime/review tests.
Python compilation and Git whitespace checks passed.

Full offline collection failed on four baseline missing-script imports
([XML](evidence/offline.xml)). Continuing outside the network sandbox exercised
more legacy tests but showed repeated unrelated baseline failures; the diagnostic
was stopped at approximately 59%, rather than repairing historical work outside
this boundary. [Progress log](evidence/offline-diagnostic.txt) and
[verification manifest](evidence/verification.json) retain the exact status.
The full suite is **not green** and the continued diagnostic did not finish.

No real vault, product deployment, PR or merge is involved. The next authorized
integration step is for the existing Agent Loop owner to compose the snapshot,
receipt verifier and budget guard, then schedule this plan through existing
tools and Reviewer. This delivery does not perform that wiring.
