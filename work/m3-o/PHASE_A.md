# Phase A checkpoint

Implemented `src/linkloom/evaluation/task_profile_ab.py`,
`task_profile_score.py` and `scripts/run_m3o_ab.py`. Public sources, original
artifact/message identities, code-point evidence spans, separate labels,
rubric and generation configuration are frozen with SHA-256 in
`docs/requirements/m3-o-quality-convergence/ab-v1/`.
13 paired sources: nine known M3-N development cases, three public holdout
cases (NOT blind), and the original known five-message failure.

Only variables: historical F prompt versus optional CROSS_MESSAGE_V1 prompt
and canonical message map; resulting request identities necessarily differ.
Model `gemini-3.8-flash`, schema, one attempt, 3072 output cap, low thinking,
omitted temperature, source, Host projection/veto and scorer are shared.
26 intended LIVE requests. Conservative reservation USD 0.079872 each,
USD 2.076672 total, grant ceiling USD 3.00; frozen price ceilings are accounting
assumptions requiring separate human authorization, not a current price quote.
Input cap 65536, billable output cap 8192; actual configured output 3072.

Execution reads sources/config only; scorer reads labels afterward. Mock uses
empty responses and the SAME old recorded failure output for both profiles.
This tests scoring and transport, not B inference. Strict replay compares every
normalized request. Raw task payloads are scored before extraction validation;
Host candidates, work coverage, erroneous grouping, fields, schema, evidence,
false rejection/acceptance and failure attribution are separate.

Evidence: `a-green3.xml`: 23 passed (8 new A checks + 15 M3-N checks).
First RED: missing module; `a-green1.xml` was an environment failure (new temp
parent absent); `a-green2.xml` 3 passed. `mock-a-final/report.json` and
`replay-a/report.json`: A=13/B=13, schema/options equal and all fingerprints
different, zero real requests. `historical.replay-score.json`: original 3-source
strict replay still recall/precision 4/5, fields 24/24, evidence 5/5.
`historical.replay-comparison.json` retains original comparison.

LIVE is default off. `--mode live --enable-live --grant <file>` is required.
The exact grant binds model, provider, manifest hash, request/cost/rate caps and
an aware future expires_at. LIVE budget has one canonical separate SQLite path
under the user's `.linkloom/m3o-ab-v1`, never the Canary ledger or output folder.
SQLite FULL synchronous reservation precedes send. Unknown/missing thinking
usage, model-version mismatch, pending reservation, retry, expiry or budget
limit stops further calls. A new output folder cannot reset the budget.

Offline reproduction (from this worktree, fresh output paths):
```
python scripts/run_m3o_ab.py --output work/m3-o/new-mock
python scripts/run_m3o_ab.py --mode replay --replay work/m3-o/new-mock --output work/m3-o/new-replay
```
Future separately authorized command only (DO NOT run in M3-O):
```
python scripts/run_m3o_ab.py --mode live --enable-live --grant work/m3-o/human-grant.json --output work/m3-o/new-live
```
Grant fields are defined by `validate_grant`, with manifest hash from
`load_freeze()['manifest_sha256']`. No grant was created or authorized here.

REAL_MODEL_SEMANTIC_EFFECT = NOT_EVALUATED. No product profile adoption.
Proceed to B; no blocking dependency remains. Final freeze/code review and
full affected regression remain part of overall acceptance.

## Overall-convergence corrections

Raw-vs-Host malformed output attribution was corrected after a RED case
(`score-taxonomy-red.xml` -> 8 passed GREEN). A legal cross-message proposal
covering two source messages initially received raw credit for only one;
`groups-red2.xml` reproduced 1 vs 2 and `groups-green.xml` passed 9 checks.
The final scorer uses raw source-message work units before Host validation,
retains original proposal count, and uses evaluation-only `groups.json` for
explicit continuation allowances. No M3-N source/Gold or prompt was changed.

The initial manifest is retained as `initial-freeze-manifest.json`; final
implementation binding includes A code, current common Host rules and LF
checkout policy. `portable-freeze.xml`: 24 passed including 15 M3-N default
compatibility checks. A hash/parse replacement race was reproduced and fixed
by parsing exactly verified bytes (`freeze-race-red.xml` -> 10 passed GREEN).
Labels/groups are deserialized only by the separate scoring phase. Final
accepted replay artifacts must match the final manifest, not the early
intermediate Mock reports. Zero LIVE calls throughout.
