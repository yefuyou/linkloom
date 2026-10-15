# Independent Technical Review

## Scope

Reviewed the worktree at base `49736a7` against
`docs/requirements/agent-recovery-optimization/SPEC.md`, the changed `src/`
runtime files, `scripts/recovery_live.py`, `scripts/reliability_probe.py`,
the recovery scale observer, the focused tests, and the retained synthetic/live
evidence. No Provider credentials, real Vault, or production data were used.

## Verdict

**PASS — no Required or Blocking findings.** The authorized P0/P1 boundary and
the P2 profile-only boundary are implemented with the existing evidence,
Reviewer, Ledger, and source-identity gates intact. The live evidence is
reported with its stop conditions and does not claim a causal before/after
improvement.

## Findings

### P0 graceful partial recovery

No blocker found. A single `task_plan/v2` query semantic error is recoverable
only for the planned `query_variant` or `subgoal_id` paths, and only when the
model supplies a null-decision Final. The Final still passes the existing
grounding, TaskPlan, citation, uncertainty, and Reviewer validation path.
Identity/source-context errors, permission/provider failures, and repeated
invalid calls remain terminal. `budget_exhausted` is kept as a budget failure
and is not converted into a successful Partial.

### P1 same-file passage coverage

No blocker found. Retrieval materializes all matching body passages from a
selected document, keeps the existing source/version/line/quote metadata and
opaque passage identity, and applies the caller cap after BM25 ordering. The
context assembler removes only duplicate `evidence_id` values, so distinct
passages from one file survive while repeated copies remain bounded.

### Live controls and uncertainty accounting

No blocker found. `recovery_live.py` enforces the 120-request, `$8`, three-hour,
serial-run boundary, one SDK client with automatic retries disabled, conservative
input reservation, and the cross-attempt canary cap. Nested Ledgers are
accounted before admission; missing Usage consumes the reservation and is not
itself a strict Provider-unknown outcome. Connection-establishment failures
(connect/DNS/proxy) are known failures, while TLS/read-timeout/remote-protocol
cases remain strict unknown. A strict unknown blocks its case, is never replayed,
and the second strict outcome stops the batch. The observed TLS `ConnectError`
cases therefore correctly stopped the comparison rather than being reclassified
as safe connection failures.

### P2 profile boundary

No blocker found. The change does not add a cache, mtime shortcut, index service,
or production performance optimization. The scale observer now reports
exclusive retrieval and verified-read buckets separately from nested
`read_notes`; retained historical buckets are labeled non-additive and the
documentation does not claim a causal speedup.

## Evidence

- Focused closure under `PYTHONPATH=src;.` and
  `LINKLOOM_RETRIEVAL_MODE=bm25` with `D:\Anaconda\python.exe -m pytest -q`
  over `test_graceful_partial_recovery.py`,
  `test_recovery_passage_coverage.py`, `test_recovery_live_limits.py`,
  `test_recovery_evaluation.py`, `test_context_assembler.py`, and
  `test_reliability_probe.py`: **49 passed**.
- Retained regression artifacts report **133 passed**, **17 live-canary-fix
  passed**, **17 P1-closure passed**, and **47 final contract tests passed**;
  the browser DOM check also covers the explicit uncertain-outcome wording.
- `work/agent-recovery/live-canary-after-02/summary.json` records 18 requests
  and `$0.15925275`: missing-evidence is insufficient and the same-file
  multi-passage canary succeeds.
- `work/agent-recovery/live-comparison-after-01/summary.json` records 63
  requests and a conservative `$0.74822475`; two successful cases precede two
  TLS provider-outcome-unknown cases, after which the batch is halted. The
  formal after trial for the same-file comparison was not sent after that
  strict-unknown stop, so that comparison remains N/A.
- The final frozen offline scorer reports 12/12 evidence/source coverage and
  11/12 exact/outcome score. `AR-F-CONFLICT` is the sole mismatch: the Product
  result reports `conflicting_evidence`, retains the independently verified
  owner fact and all three conflict sources, while the unchanged Gold fixture
  expects a refusal with no facts. The same mismatch is present in the retained
  earlier `first-12.scored.json`; it is recorded as a pre-existing fixture
  semantic limitation, not a regression introduced by this work.

No code or data was changed as part of this review beyond this report.
