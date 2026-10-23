# AMI Discovery P2: integration and Canary handoff

Base: `1bfd910bb3a53a902a708ca2d00a39f4dde68091` (master, verified remotely).
Original local fix, preserved unchanged: `f68c3e17d0de0c88d5ace0f84cf2b3c5a25fccda`.
Original acceptance remains **455 passed**, not replaced by this integration run.

## Minimal integration and review

Only `text_scopes.py` changes production behavior (+101/-11). It is byte-for-byte
identical to the accepted local fix. The two generic regression files are also
unchanged; the existing offline CI task adds these two files (109 tests).
No #16 archive, Gold, F prompt, task-recall/v3 schema, work identity, source
validation, NON_ASSERTED, UNKNOWN, P4 or Reviewer contract is modified.

The original lexer opened a quote at AMI `'Kay` offset 868 and masked
`[868,7945)`, including the task at `[974,1049)`. The rejected patch used a
nesting-blind next-apostrophe scan: in the exact P2 it skipped opener 10 upon
seeing inner opener 51, leaving only `[69,70)` as the outer range. The accepted
structural matcher restores `[10,70)` and refuses the task at `[17,35)` with
NON_ASSERTED_TASK (zero VALID/PARTIAL); it still leaves real work after the
quote visible. Unicode offsets always refer to unchanged source code points.

Independent review: **PASS, no new P0/P1/P2 blocker**. The reviewer independently
reran 193 integration tests and 2808 complete nested-quote combinations. General
English punctuation lexing contains no AMI work sentences or role regexes.

## Local integration checks

- **865 distinct tests passed, zero failures/errors/skips**, 161.55 seconds.
  Selection: the union of all pytest selectors in the current task-investigation
  CI job, plus source_identity_contract, semantic_ingestion_prompt_contract and
  offline_source_controller. This includes the merged field/recall, P2-A/P2-B,
  source permissions, work identity, P4 and Reviewer contracts.
- **116 focused tests passed**: 109 self-contained tests shipped in this PR,
  plus 7 real AMI replay tests from the original worktree. Those 7 require #16's
  sealed files and are deliberately not represented as self-contained new CI.
- `git diff --check` passed. No real vault or paid Provider was used.
- Initial broad run: 859 passed / 6 environment failures (missing installed
  dependencies in cold subprocesses and SDK imports). A CI-equivalent isolated
  `.[dev,smoke]` environment plus the host's optional SOCKS dependency fixed
  those issues. The same 6 tests passed on unchanged master; the final complete
  candidate run above passed. No source or test expectation was changed for it.

## External sealed response replay

#16's sealed sources/responses and `scripts/run_ami_semantic_canary.py` remain
external, anchored at `f8e03f24bb53748cad401834253ff1ebfc01a922`.
The offline replay utility and 7 real-case tests remain on the preserved local
fix worktree. Run them with the **candidate's `src` first** in PYTHONPATH and the
original worktree root afterwards. The receipts verify the imported module is
from candidate/src, the immutable file seal, every exact request and all claims;
network connections are blocked. No output or historical score is overwritten.
The live Canary runner can likewise be supplied externally from #16, using the
candidate source and a fresh output directory; no paid run is authorized here.

Both master and candidate processed the same **28 responses / 75 raw and parsed
F claims**, with identical requests and claim payloads. This is the original
general F profile (`task_profile=null`), not a new task-recall/v3 model evaluation.

| Output / refusal | Master | Candidate |
| --- | ---: | ---: |
| VALID | 0 | 0 |
| PARTIAL | 0 | 4 |
| NON_ASSERTED_TASK | 4 | 0 |
| TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS | 1 | 1 |
| Known meeting-event model error, raw claim count | 1 | 1 |
| That known error visible as PARTIAL | 0 | 1 |

The newly visible meeting-time PLAN is the pre-existing model error
`ES2002a:chunk:0006:claim:0`, source `[4755,4802)`. It remains PARTIAL,
SUPPORTING_ONLY, UNCONFIRMED, deadline_role UNKNOWN and deadline null. It is
**not a successful task recovery**. Known raw-model false-positive delta is 0;
known visible Discovery false-positive delta is +1. Separately, the exact
synthetic nested-quote regression changes erroneous PARTIAL from 1 to 0.
The four PARTIAL records are not four verified tasks. Overall semantic
precision/recall and the total semantic false-positive population are NOT_EVALUATED.

## Bounded complex-input check and remaining risks

Thirty-second process cap; three timings per size, medians below. Input cycles
four directional quote pairs, encloses one task and leaves another task outside.
Outer scope, range count, hidden inner work and unchanged later offsets pass.

| Depth | Characters | Candidate quoted_ranges seconds |
| ---: | ---: | ---: |
| 1000 | 2045 | 0.003196 |
| 2000 | 4045 | 0.006523 |
| 4000 | 8045 | 0.012755 |
| 8000 | 16045 | 0.031038 |
| 16000 | 32045 | 0.057183 |

The new matcher has six fixed closer states, constant-pass scanning, no recursion
or exponential backtracking. Existing consumers still mask overlapping ranges
repeatedly: at depth 4096, master/candidate unquoted_text took 0.514/0.501 seconds,
and NON_ASSERTED took 0.648/0.664 seconds. Their quadratic worst-case masking
cost predates this patch and remains; this sample exceeds the 8000-character
Canary chunk bound. These measurements are evidence, not fragile CI time gates.
Existing natural-language apostrophe ambiguity, including plural possessives
inside curly quotations, also remains. Do not claim general semantic correctness.

For a separately authorized real Canary, use the exact remote PR head recorded
in the handoff, not the old #16 head or an unreviewed working tree. This change
establishes engineering readiness only. Keep the PR Draft; do not merge master.

Production SHA-256: `a3855a152672c6a6c78a639d4deb846b63a05e094861bd10ca88f2766fdbc8a8`.
Sealed input manifest SHA-256: `d4a5b7da6318acc217551abf481cce6a33a11426c20f5c0b1f7aee2054f82fd9`.
Final local JUnit SHA-256: `80c2950f27e49df5e739567b81734a135b735761734cbb3eb18407395f9ae967`.
