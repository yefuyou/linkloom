# Validation evidence

All data is synthetic. No real vault, old Gemini Ledger, master, PR #3 or another
checkout was modified. The isolated checkout began at `cd668f3`.

## Local checks

| Check | Result |
|---|---|
| New plan/provider/dataset/evaluation/scoring unit contracts | 48 passed |
| Unified Ask, source investigation, feedback and adaptive HTTP regression | 41 passed |
| Final adaptive HTTP + existing classifier/rule planner | 27 passed |
| Browser DOM races, per-request permission, rejected/stale Final hiding | Passed |
| Strict evidence scorer after same-file reference normalization | 9 passed |
| `py_compile` for new runners/policy; `git diff --check` | Passed |
| Full unit/integration collection | Blocked by four historical missing script imports |
| Collectable broad suite | Stopped at 43% after widespread historical failures; not a passing full-suite claim |

Counts overlap; do not add them into a fabricated total. The task's final
checks are the focused product and safety contracts, not historical benchmark
restoration. New tests do not skip or weaken existing assertions.

The missing imports are `scripts.run_memoryagentbench_g3_pilot` (three modules)
and `scripts.run_stage_c_context_memory_loop` (one). A source archive of the
exact baseline under this checkout's ignored `work/` reproduced 89 failures /
45 passes in the historical Gemini/TaskPlan tests. Many expect old model IDs
or temperature behavior incompatible with the baseline's pinned Gemini 3.8.
Unrelated failures were not repaired or counted as new regressions.

## Independent reviews and repairs

1. A reviewer identified the decision-ambiguity fallthrough. A focused HTTP
   regression failed with 202/model-factory invocation before the fix, then
   passed with `DECISION_SCOPE_AMBIGUOUS` and zero model calls.
2. Provider review checked hard admission, serial leases, zero SDK retries,
   three-trial bounds, consecutive-uncertain stop and historical Ledger isolation.
   A small persistence regression ensures a failed terminal Ledger write stops
   the current instance before any subsequent SDK call.
3. Evidence review identified insufficient-plan scoring, extra-fact acceptance,
   and C source-path/VerifiedRead identity mismatches. Independent reproduction
   tests now pass. Scoring revalidates the actual persisted plan and cross-checks
   HTTP citations against saved Runtime reads, workspace and quote hashes.

## Live acceptance boundary

Gemini 3.8 Flash preflight used the official Developer API endpoint, pinned model,
`vertexai=False`, 130-second timeout and `HttpRetryOptions(attempts=1)`.
Credential presence and successful invocation establish access; the SDK does
not expose the billed account principal. Token accounting includes thinking.
There are no hosted tools, media, caching or SDK tool execution.

`docs/evaluation/adaptive_planning/browser-final.json` and `browser.png` show the
new unrecognized question “Who manages Marble Gate, and who is that manager's
direct supervisor?” executed from the real Ask form. The accepted result is
Evan Grey, with Nora Bell as the verified intermediate owner. It used one model
planning request and six Runtime turns, including two searches and three
verified reads. Reviewer recorded `evidence_sufficient`.

The first independent trial of that case had a successful Runtime/Reviewer but
the evaluation harness's Windows file-copy failure lost the HTTP Final capture.
It remains `CAPTURE_FAILED` in the primary denominator; the second browser trial
is separate evidence, not a replacement first-trial score.

The frozen final partition's prompt/product behavior was not tuned from its
results. Generic scoring/capture defects were corrected offline, preserving
original raw results, original reports and their implementation fingerprints.
Full private artifacts remain under `work/adaptive-planning`; committed evidence
contains synthetic results, safe request cost records and hashes, not credentials
or the raw Provider Ledger.
