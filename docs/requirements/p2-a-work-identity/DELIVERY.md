# P2-A delivery: READY_FOR_P2_INTEGRATION

Base: `33390e2eeaad71d215e279b2f724cae672ea4f80`.
Branch: `codex/m3d-fix-work-identity`; normal push to `yefuyou/linkloom`.
No changes to master, the original M3-D branch, PR #13 or PR #14. No merge,
force push, paid model call, prompt change, Gold/Frozen change or UI change.

## Root cause and minimal fix

The original v1 ID hashes workspace, source version, evidence reference and raw
start/end. F correctly proposed two works inside one exact raw sentence, so the
IDs collided. Discovery treated their different descriptions as competing
extractions and withheld both. G received no task endpoints and Product returned
UNKNOWN, even though both works were source-verifiable.

Current TASK_RECALL_V3 Discovery now builds v2 IDs from workspace, immutable
source version, canonical message/evidence and the sorted unique description
evidence ranges. Source verification determines those offsets; no model index,
randomness, time, role, assignee or deadline is part of the work identity. The
raw window can change without manufacturing a different work ID. Description
part order and duplicates are canonicalized. Ambiguous source occurrences fail
closed. Same-work inconsistent extractions still share an ID and are quarantined;
independent work in the same raw fragment survives the filter.

## Actual HTTP evidence: BEFORE FAIL then AFTER PASS

All requests used the normal Product HTTP endpoint, Provider adapter, G planning,
Runtime, Reviewer and persisted cold read with **scripted transport only**.

| Receipt | POST | F proposals | Distinct IDs | Discovery observations | G endpoints | Product | Cold GET |
|---|---:|---:|---:|---:|---:|---|---:|
| [BEFORE](evidence/before-http.json) | 202 | 2 | 1 | 0 | 0 | UNKNOWN | 200 |
| [AFTER](evidence/after-http.json) | 202 | 2 | 2 | 2 | 2 | PARTIAL, Reviewer accepted | 200 |

BEFORE shared ID:
`task_observation_v1_546ededa0395efc434656458bb30a1026a98e918325b2bb39f7bb33c766d2632`.

AFTER work IDs:

- `task_observation_v2_497e55c86d153d2b692409e10bcf3d21a98b431de35806195690be3735aaa488`
- `task_observation_v2_bd83320ac03cbe27537bb88a0a4c820a7869a4189b453007322652fddbdca343`

The before test fails with `assert 0 == 2`; the after test passes. Product retains
both task descriptions, their shared owner and date. Runtime executes
`inspect_task_context` and `query_task_relations`. Semantic/P4 approval is never
requested or generated; conclusions remain empty, authority SUPPORTING_ONLY,
relations UNCONFIRMED. The original sentence omits project/vendor, so PARTIAL
is the expected honest result. A separate regression checks one fully populated
VALID task alongside one task with UNKNOWN fields.

See [compact HTTP/identity summary](evidence/http-summary.json) for receipt hashes.

## Compatibility and source integrity

- The ID prefix selects strict v1 or v2 reconstruction in `__post_init__` and
  `verify`; forged/unknown IDs do not receive a compatibility exception.
- Legacy/V1 and FIELD_GROUNDED_V2 Discovery keep the original v1 generation.
  Direct new field-bound construction defaults to v2. Existing saved field-bound
  v1 observations explicitly replay v1 during verification.
- Serialization adds no default fields. ObservationRef shape and payload hash
  remain unchanged, including exclusion of recorded_at. The fixture captured
  from the fixed baseline proves byte-equivalent payload/ref round trips.
- [Baseline v1 HTTP result](evidence/baseline-v1-http.json) was produced using
  read-only baseline source code. [New-code cold read](evidence/legacy-v1-cold.json)
  returns HTTP 200, PARTIAL, with unchanged task_review and external ref.
- No ID aliases, relation rewriting or automatic P4 migration exist. Existing
  P4 records still bind their original refs; v2 observations cannot inherit
  those records. A persisted-sidecar regression verifies this without writes.
- Source change, evidence revocation and Workspace isolation remain fail-closed
  during Discovery and Product hot/cold reads. No repeated provider calls occur
  during cold read or permission checks.

## Changed files and tests

Production (only two files, 50 additions / 10 deletions):

- `src/linkloom/semantic_ingestion/task_observation.py`
- `src/linkloom/semantic_ingestion/task_observation_discovery.py`

New independent regression files:

- `tests/unit/test_p2_work_identity.py`: 15 tests, including parameterized source
  cases; shared raw, order, fields, duplicate work, contradictory roles,
  conflict isolation, raw window, canonicalization, ambiguity, tamper rejection,
  baseline v1 refs, source permissions and P4 noninheritance.
- `tests/integration/test_p2_work_identity_http.py`: 7 cases for full HTTP flow,
  reversed proposal order, UNKNOWN fields, update/revoke/foreign hot/cold reads
  and legacy REPLAY v1 persistence/cold read.
- `tests/fixtures/p2_work_identity_v1.json`: immutable v1 observations and refs
  captured before production edits from the fixed baseline.

Task-only SPEC, review and evidence are stored in this directory. Temporary
scripts/databases remain under work/; nothing was deleted.

## Validation and independent review

- BEFORE HTTP: 1 expected failure; independent identity contracts: 9 RED failures.
- Focused old/new Observation, Discovery and field contracts: **98 passed**.
- New identity/source/P4 regression: **14 passed**; added complete/unknown case:
  **1 passed**.
- New normal Product HTTP cases: **7 passed**.
- Full repository task-investigation gate plus affected source/field/HTTP
  regressions: **675 passed, 5 environment failures** initially. All five cold
  subprocess cases then passed targeted verification: 2 after explicit Python
  UTF-8, remaining 3 after sequential rerun (fixed 3-second client timeouts under
  local load). Thus all **680 selected cases have passing evidence**, plus the
  additional VALID/UNKNOWN case. Already passing cases were not rerun.
- Original failures are retained in `regression.log/xml`; targeted runs are
  `cold-subprocess-utf8.log/xml` and `cold-subprocess-serial.log/xml`. Exact selected
  tests/command are in `regression-command.json`. No production or existing test
  change was made to hide environment failures.
- Source, tests and task Markdown pass whitespace checks. Raw pytest XML/logs
  are preserved unchanged, including diagnostic whitespace. Validation is local;
  CI was not run for this branch.
- [Independent read-only Reviewer](REVIEW.md): **P1 = 0, P2 = 0**. Reviewed identity
  stability, historical compatibility, source integrity, duplicates and P4 refs.

All P2-A acceptance criteria are met. Next step is explicit integration of this
branch into the blocked work; this delivery performs no merge or PR mutation.
