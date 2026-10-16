# Independent review: evaluation qualification

Status: **APPROVED_FOR_PREPARATION**

This is the independent re-review after the earlier `NEEDS_CHANGES` result.
The decision covers preparation and handoff evidence only. It does not claim
the historical 249-node Core suite is green, authorize a new Final run, or
authorize Gemini or any other real Provider request.

## Closed findings

- **Core Gate:** `CORE_GATE.json` is `PASSED` with 12/12 targets and 41/41
  tests passed. Every target reports zero failed, skipped, and error tests;
  raw JUnit and logs are retained under `evidence/core-gate/`. The report
  records `network=deny` and `provider_sdk_requests=0`. The observations cover
  backend counting, SourceFreshness identity change, SQLite cold resume, and
  the other requested guards; no target is an empty or skipped check.
- **Tracked clean reproduction:** `CLEAN_EXPORT.json` records identical source
  and export index trees (`c80979c4e85b20c9786b359dfc1e49ae50a6788f`), no
  index differences, 947 tracked files, and byte matches for the 11 gate and
  runtime files. `CLEAN_ENVIRONMENT.json` records fresh Python 3.13.5 and the
  fixed 33-package dependency set.
- **Fresh 249-node evidence:** `CORE_REGRESSION.json` records 249 nodes on
  each exact commit. Both sides retain raw 209 passed / 40 failed counts. The
  normalized comparison is 209 stable passes, 2 original failures, 38
  unexecutable historical-input cases, and zero new failures, fixes, not-run
  nodes, or environment classifications. The packet includes all 39 changed
  paths with before/after blob identities, archive verification, raw JUnit,
  logs, and portable hashes.
- **Historical provenance:** `INPUT_PROVENANCE.json` identifies 11 missing
  original files affecting 38 nodes. Git blob/log lookup and the read-only
  main-checkout probe found no trusted originals; no replacement inputs were
  created. Those nodes remain `unexecutable` rather than fabricated passes.
- **Fail-closed classification:** the current regression runner keeps unknown
  artifact errors and timeouts as failures. Only the demonstrated long
  Windows artifact-path failure is classified as environment; exact phase
  9/11/14 missing-input cases are unexecutable. The CLI returns non-zero
  unless every node is `stable_pass`; the independent classification contract
  suite has 7 passing tests.
- **Static and historical protections:** preflight/audit evidence passes and
  rejects D06/F07 goal mismatch, unreachable targets, invalid Gold/source
  spans, and mechanical cross-partition renames. The old V3 files, Gold,
  scores, and freeze artifacts remain byte-stable; the changed-file inventory
  contains no `src/` or old-frozen-path modification.
- **Model-input boundary:** Gold is author-visible preparation data and is
  excluded from public model input. Reports retain `final_execution=NOT_RUN`
  and `real_provider_calls=0`; no new Final case ran and provider budget is 0.

## Remaining limitations

The historical comparison is intentionally not all-green: two original
assertion failures remain, and 38 nodes cannot run without missing historical
inputs. The synthetic 41-test Core Gate proves offline runtime contracts are
observable and fail closed; it is not a model-reliability result and does not
replace the historical comparison.

Prepared Final cases are author-visible and static. Any later real or paid
evaluation requires separately approved identity, protocol, and budget.

## Verdict

`APPROVED_FOR_PREPARATION`. No issue from the earlier review remains a blocker
for producing the preparation/approval packet. The historical limitations
remain blockers for claiming the old Core suite is fully green.
