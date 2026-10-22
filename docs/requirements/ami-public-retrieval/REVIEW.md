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
