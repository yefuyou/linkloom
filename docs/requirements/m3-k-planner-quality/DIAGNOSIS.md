# M3-K diagnosis before repair

Base is remote G HEAD `167a46694048e9ae6dd26879c2c6986fed8c0a15`.
Read: G SPEC, DELIVERY, REVIEW, EXAMPLES, three live requests and results,
live provenance/metrics, Planner Validator, typed signature and 40 existing tests.
The original saved artifacts and old scores remain unchanged.

## What INVALID_INSPECTION proves, and what it cannot prove

Live ordinal 11: one Gemini generation, 5,170 input + 667 output = 5,837
tokens, response SHA
`7d7deb76bbd0f6175081e96913c9d541e94550cbd69c629fc259b7835af88bb1`.
The original runner did not save rejected arguments. G provenance explicitly
records this omission. There is no retained rejected plan to inspect or replay.
Recovering an argument from a cryptographic digest is not a viable diagnostic.
No new provider call is authorized for this investigation.

The validator branch narrows the cause to at least one of:

- handles count was not exactly one;
- the supplied endpoint was ProjectRef/ChangeRef rather than ObservationRef;
- field was not owner/project/deadline/scope/evidence;
- predicate or target_kind was nonempty;
- missing_entities was nonempty.

Unknown handles would have failed earlier as UNKNOWN_HANDLE, so cannot alone
explain this specific error. Empty/multiple handles, a known project endpoint,
empty field or mixed operation fields all satisfy the emitted flat schema yet
fail the actual inspection contract. They are reproducible structural defects,
not reconstructions of the lost live output.

**Attribution:** an inspection-contract violation was observed by G. The exact
offending field and whether it was a model mistake or a false rejection cannot
be established from saved evidence. No evidence justifies claiming a proven
Validator false rejection. The schema is demonstrably underexpressive: handles
mix endpoint types and allow 0..4 items, while other operation fields are global.
The existing adapter rejects oneOf/anyOf and projects away maxItems/maxLength.
Changing that F/provider boundary is outside M3-K. M3-K improves compatible
descriptions/capability inventory and reports exact validation violations while
keeping Host validation strict. Its offline schema projection test makes no claim
about real Provider acceptance or model quality.

## Why the simple owner plan grew

Ordinal 0 asks who currently owns sensor commissioning. Its snapshot contains
only old as a typed task, but also admits s_new, s_change and s_counter text.
Gemini proposed:

| Step | Operation | Evidence-gap assessment |
|---|---|---|
| step_1 | inspect owner(old), s_old | Directly relevant. |
| step_2 | discover updated task, s_new | Potentially justified: current ownership may need a newer observation bound by Host. Not conclusively excessive. |
| step_3 | discover supplier change, s_change/s_counter | No explicit link to missing owner evidence. One conservatively identified unjustified step. |

The broad instruction to inspect contradictions and find concrete changes did
not confine that work to the original question. The old schema had no per-step
goal-related gap. The scorer credited the extra categories and never penalized
unnecessary work. Thus category coverage 6/7 and successful plan admission did
not establish planning efficiency. M3-K separately reports proposed/accepted/ready
steps, excess-step violations and a manual audit of saved live outputs. Nonempty
gap prose is observable model justification, not semantic proof of relevance.

Ordinal 8's compound plan has eight steps, including membership and investigation
of another task. Those may be useful to scope 'which tasks'; they are not labeled
excessive merely because there are more steps than required category labels.

## Confirmed Host defects

The old typed key already hashes complete endpoint refs. Different ObservationRef
or ChangeRef versions therefore do **not** incorrectly merge by themselves.
However, the key omits source_handles and bound spans, time scope, dependencies,
workspace for endpoint-free operations and unknown-entity search requirements.
Consequently source-only discoveries, ProjectRef evidence revisions, different
time windows/premises or different unknown entities can merge incorrectly.
Question paraphrases on the same typed work should still merge.

The old semantic_queries exposes all uncompleted relation queries, including
REVISES_PLAN before its SAME_TASK receipt exists. Structural DAG validity alone
does not mean a premise has been verified. Receipts also lack semantic outcome,
so UNKNOWN/REFUTED/CONFLICT cannot be distinguished from positive support.
Completed source-only operations are not bound back to their prior evidence
on replan. These are independently reproducible defects covered by M3-K tests.

## Evidence boundary

Original live validity remains 2/3, coverage 6/7, calls 3, tokens 18,055.
Only two accepted live outputs can be revalidated offline. Under the stricter
gap contract the owner output is rejected with two unexplained discovery steps;
the compound output remains valid. The third output is explicitly not replayable.
This is revalidation of saved arguments, not new model predictions or an updated
live score. Authored M3-K responses are contract examples, not evidence that Gemini
planning improved. Newly incurred paid model calls and paid model cost are zero.
