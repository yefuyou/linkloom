# Independent review: AMI public human Gold retrieval

## Verdict

**PASS for the reviewed implementation and sealed `run-02` evaluation.** The four previously demonstrated scorer-integrity blockers are fixed and covered by focused counterexample tests. The planned `run-03` replay on `data-04` remains an operational follow-up before calling the milestone fully complete; it must preserve the sealed-input and fixed-code fingerprints.

Review was read-only for implementation files. The only review artifact written by this reviewer is this document.

## Scope and evidence

- Worktree: `codex/ami-public-gold-benchmark`, reviewed at the current uncommitted implementation over base `46b819fa39ace26de2c5470f7438b56df523c560`.
- Reviewed `common.py`, `prepare.py`, `predict.py`, `score.py`, the AMI NXT adapter, the focused evaluation tests, the AMI retrieval SPEC, and the generated `data-04`/`run-02`/`score-02` artifacts.
- Independent focused test command:

  ```text
  PYTHONPATH=src python -m pytest -q --basetemp=work\pytest-ami-review-final-01 tests/unit/test_ami_public_evaluation.py
  ```

  Result: **8 passed**. This includes source-ref/logical-path tampering, context source-ref tampering, empty-selector inconsistency, partial-DA retrieval, and `NOT_STARTED` cases.

- The existing 94-test regression result and the 21-test red-to-green scorer/adapter result were supplied by the implementation run and were not duplicated here.
- `score-02/scores.json` has `EVALUATED` BM25, Dense, and Hybrid methods, `scheduled=56`, `eligible=51`, `not_evaluable=5`, and `not_executed_eligible=0`. Its numeric method/split metrics are byte-for-byte equivalent to `score-01` metrics despite the scorer SHA changing.
- `run-02/run_manifest.json` records `network_attempts=0`, `denied_data_opens=0`, cached local Dense weights, `provider_calls=0`, and all three retrieval methods as `RUN`.
- `data-04` has the expected 49,806 words, 6,981 native DA passages, 56 fixed queries, 51 eligible and 5 N/E queries, 17 unsegmented words retained only in canonical/F material, and per-meeting word-span/chunk validation marked true. Independent hashing found that `data-03` and `data-04` differ only in the input/evaluator manifests; passages, queries, transcripts, qrels, provenance, and upstream metadata are byte-identical.

## Previously blocking cases rechecked

1. Ranked source provenance now rejects tampered `source_ref` and `logical_path`, and checks the expected workspace/content/quote hashes.
2. Context provenance now requires the single expected source reference for each selected evidence item and validates context workspace/query.
3. Selected evidence must be a subset of selector output and the retrieved prefix; clearing `selection.selected_ids` now raises instead of inflating final-context coverage.
4. Partial DA retrieval now records required, retrieved, missing, selected, and rendered piece IDs plus coverage and reports `PARTIAL_DA_RETRIEVAL`.
5. Missing/unexecuted predictions now produce `metrics=None`, `evaluation_status=NOT_EVALUATED`, and an `EXECUTION_*` cause. Aggregation separates Gold eligibility, unexecuted eligible queries, and the metric denominator.

These checks remove the earlier silent-acceptance paths. The scorer still reports the evaluation as `PUBLIC_HUMAN_GOLD`, keeps the fixed query/reference boundary, and explicitly marks Product E2E and other non-scoped tasks as not evaluated.

## Remaining operational follow-up

Run the planned `run-03` from `data-04` with the final committed implementation SHA, then seal and score it in a new output directory. Do not overwrite `run-01`, `run-02`, `score-01`, or `score-02`; acceptance of that final artifact requires the same seal, source/query hashes, 56/51/5 cohort counts, isolation counters, and method status checks above.

## Final operational readback

The follow-up is now complete for the final implementation:

- `run-03/run_manifest.json` records Git SHA `3dda2d25bb4806d64e4b1832cf2eb30d26d28856`, matching the reviewed `HEAD`; all 30 recorded source-code hashes and the scorer hash match the current source files.
- `run-03/output_seal.json` is sealed. Both prediction and manifest hashes independently match the sealed files and the packaged copies.
- Final isolation is `network_attempts=0`, `denied_data_opens=0`, and `gold_data_opens_during_prediction=0`; BM25, Dense, and Hybrid are all `RUN`. The local model snapshot and every recorded model file hash are present in the run manifest; provider calls and cost are zero.
- `score-03` evaluates all three methods with `56 scheduled / 51 eligible / 5 N/E`, `metric_denominator=51`, and `not_executed_eligible=0` in the all split. Its numeric method/split objects exactly equal `score-02`; the packaged `scores.json` exactly equals `score-03`.
- The package `validation.json` file list has 13/13 matching SHA-256 values. Its input and Gold manifests bind to `data-04`, whose passage/query/transcript and evaluator file hashes verify; source validation reports full roundtrip and all per-meeting positions, hashes, budgets, and chunks valid.
- `offline-tests.xml` parses as 94 tests with zero failures, errors, or skips. No new test or model run was needed for this readback.
- `REPORT.md` reports the public human-link scope, fixed ES2005 diagnostic limitation, no Product E2E/blind/generalization claim, cached local model, zero provider calls, and CI `NOT_RUN`; the stated claims are consistent with the final artifacts.

**Final independent review verdict: PASS.** At the time of this readback, `validation.json` still contained the coordinator's temporary “final replay operational verification pending reviewer readback” text; the coordinator should replace that status with this final PASS and refresh any enclosing package seal after doing so.
