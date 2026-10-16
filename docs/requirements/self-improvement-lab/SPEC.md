# SPEC: Controlled Self-Improvement Lab (Accepted User-Authorized Boundary)

## Boundary and problem

The bounded investigation emits structured runtime traces but lacks a finite experiment for testing data-only improvements against measured DEV failures. `scripts/self_improvement/run.py` runs that experiment serially and produces evidence. It does not modify production behavior, permissions, validators, memory, or the real vault. The user-authorized boundary is this branch and its lab artifacts; no additional approval gate is introduced.

## Inputs and isolation

- Keep separate public/Gold files for each partition: `dev_public.json`/`dev_gold.json`, `validation_public.json`/`validation_gold.json`, and `final_public.json`/`final_gold.json`.
- Execution receives public synthetic cases only. Gold is loaded after raw results are sealed and never enters Runtime, the generator, candidate JSON, or controller context. Validation and final are never exposed to generation; Gold contents are not read or copied into artifacts.
- Reuse `tests/integration/run_bounded_multihop.run_case`, existing Runtime, `FakeModelAdapter`, Tool Ledger, source/workspace checks, and grounding. Reuse the reliability `_score_row` helper where its row contract fits; otherwise record N/A rather than inventing a score.

## Candidate contract

Candidates are canonical JSON with exactly these top-level fields: `id`, `change`, `query_mode`, `read_mode`, and `limit`. `id` is the candidate identity; `change` is an inert 10–1000 character description and is never executed. `query_mode` is `literal` or `relation_terms`; `read_mode` is `all`, `relation_candidates`, or `first_hit`; `limit` is exactly 2, 4, or 6. Unknown keys, wrong types, out-of-range values and duplicate identities are rejected. Execution fields accept only the listed enums/integers; they cannot carry code, paths, shell instructions, prompt templates, tool names or expressions. Those restrictions do not keyword-filter the inert explanation text.

`relation_terms` subclasses the existing `SubgoalQueryStrategy`, preserves its parent verified-entity binding, and only replaces the known question-template words with the direct terms `accountable owner` or `reports directly`. `all` routes all bounded hits; `relation_candidates` routes the first hit plus relation-keyword candidates; `first_hit` routes only the first hit and may be rejected by the gates. Only these data fields reach the lab adapter. Verified dependency binding, the read-only tool allowlist, citation checks, workspace/source identity, stop-on-error, the original `max_steps=20`, and safety budgets are immutable; there are no policy changes. `change` is never used to alter execution.

## Experiment protocol

1. Run and seal the DEV baseline. Project only structured failure signals from its traces: case identity, outcome/reason, calls/turns, context bytes, grounding/citation/binding flags, refusal category, and safe error category. Exclude expected answers, Gold, validation, and final/heldout values.
2. Generate at most 6 rounds and at most 3 candidates per round from those signals. Validate JSON before execution and run candidates one at a time under identical public DEV conditions.
3. Apply a per-case and aggregate DEV gate. A candidate must improve its targeted failure without protected regressions. Any illegal call, invalid citation, workspace/source-binding failure, or safety regression vetoes it; a bad case cannot be averaged away.
4. Only DEV survivors run on separate public validation data, with the same gates. Retain raw results and explicit reject/veto reasons.
5. Select at most one validation survivor for the separate final public set. Run the final heldout once for the baseline and, if selected, once for that single winner; if none survives, run/report the final baseline as `no_finalist`, never as a promoted candidate. Run one full pytest regression after final scoring, as requested by the user. Always finish the configured experiment, even when every candidate is rejected.

## Measurements and acceptance

Each baseline/candidate/partition report has fixed denominators and per-case before/after deltas for total tool/model calls, context UTF-8 bytes, task accuracy, grounding/citation support, and refusal outcome/reason. It also reports runtime failures, N/A rows, candidate identity, and veto reasons. Dev improvement, validation survival, and final selection require no illegal call, citation, workspace, grounding, or protected refusal regression.

## Optional live boundary

Live Gemini is optional and limited to final candidate generation/verification; offline gates remain authoritative. Use one actually compatible Gemini model newer than 2.5 (never Gemini 2.5), serially, with at most 100 generation calls and a conservative USD 5 reservation. SDK retry is zero; never resend an uncertain outcome, and stop the live batch after the second strict `uncertain`. Reuse a persistent ledger. Raw requests/responses, credentials, and transport diagnostics stay in ignored `work/`; unavailable live access is reported, not converted to success.

## Artifacts, safety, and non-goals

Raw traces, candidate JSON, sealed outputs, scorer inputs, and the ledger live under ignored `work/self-improvement-lab/<run_id>/`; only compact sanitized evidence documents and a summary of at most two pages are retained for review. No real-vault access/writeback, production strategy promotion, JSON code execution, new dependency, parallel candidate execution, or broad optimizer is allowed. The report must disclose that final heldout data is synthetic and author-visible, so it makes no strict blind-human generalization claim.

## Internal implementation plan

1. Add the runner, `dev_public`/`dev_gold` (and validation/final) path/schema checks, and frozen public identities.
2. Add the trace-to-failure projection and strict candidate parser/hash.
3. Add lab-only adapters and serial DEV execution through the bounded runner; preserve the original max-20 Runtime budget and make `change` descriptive only.
4. Add Gold-after-seal scoring, metrics, safety vetoes, DEV/validation gates, at-most-one final selection, and `no_finalist` handling.
5. Add optional persistent-ledger live verification with the stated caps.
6. Emit sanitized evidence, preserve limitations, run baseline pytest once and final pytest once, and run the one-winner final heldout comparison once.

## Acceptance criteria

- Serial bounds are enforced: <=6 rounds and <=3 candidates per round.
- Generator inputs are proven DEV-trace-only; no Gold/validation/final data reaches generation, candidate JSON, Runtime, or controller context.
- Invalid/executable JSON fails closed and only the whitelist affects the two permitted bounded targets.
- DEV and validation compare fixed per-case before/after metrics and veto every illegal call, citation, workspace/source-binding, grounding, or protected refusal regression.
- At most one candidate reaches final; reject-all still reports final baseline and runs one full pytest regression.
- Offline artifacts are complete/sanitized; live caps, ledger, zero-retry, uncertain stop, and compatible-model check are evidenced when used.
- No production permission, validator, memory, Runtime, or real-vault state is changed, and author-visible synthetic heldout limits are stated.

## Learning reflection

Planner learned that existing Runtime, Tool Ledger, grounding, and reliability scoring form the evidence boundary; only bounded data configuration should be searched. Reviewed project contracts, bounded-multihop, reliability, controlled-strategy, Provider boundary SPECs, and source entrypoints. Next: Worker implements this protocol; Reviewer checks sealed artifacts and evidence.
