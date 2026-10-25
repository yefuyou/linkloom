# Independent integration review — 2026-10-25

## Verdict and boundary

**PASS for independent Draft submission.** The
reviewed production tree has no remaining concrete P1 finding after the pending
request fix below. This is a code/scope review of the independent Draft boundary,
not approval to merge, not completion of the remote CI gate, and not
acceptance of the final combined #25 UI.

The product status remains **DRAFT_READY_PENDING_UI_INTEGRATION**. The coordinator
has supplied the final frozen-tree recovery suite, Chromium acceptance and
latest #25 shadow results, independently inspected below. Exact commit/remote
head and CI results remain coordinator-owned publication receipts. A pending
check must not be represented as a passing result.

Reviewer was independent and read-only except for this new report. No production
code, tests, original review, delivery or captured evidence was edited by this
reviewer. No Provider, key, real vault or business data was used. Tests and browser
execution remain coordinator-owned; this reviewer inspected their artifacts and
the actual code rather than duplicating the full regression suite.

## Governing scope and inspected tree

Read: `AGENTS.md`, project SPEC/development/roadmap/role/acceptance contracts, the
original feature `SPEC.md`, `REVIEW.md`, `DELIVERY.md`, and this directory's
`SPEC.md`, `INTEGRATION.md`, regression summaries and compatibility evidence.
The active work is a bounded continuation of the roadmap's read-only task
investigation/proof work, not real-vault mutation or a new business feature.

Actual base/HEAD during review:
`f2ce6b82d635a7b78b17a6d100556c354f7a461a`; branch
`codex/task-relation-review-closure`, with the feature still uncommitted at the
review checkpoint. Examined production scope:

- `src/linkloom/ui/task_review.py`: complete service/journal/capability,
  projection, canonical proof capture, readback, failure and recovery paths.
- `src/linkloom/ui/server.py`, `__main__.py`: opt-in, local HTTP API, Host/Origin
  checks, fixed/shared sidecar composition and close lifecycle.
- `src/linkloom/ui/static/product-task-review.js`: escaped evidence, local
  statements/history, source invalidation and scoped asynchronous updates.
- `product.js` 16-line post-render hook and `product.html` script include.
- `.github/workflows/offline-pr.yml`, the new backend/cache/HTTP/Host/recovery/
  commit-boundary tests, and `tests/ui/task_review_dom_test.cjs`.

Final production source reviewed:

| File | SHA256 |
|---|---|
| `src/linkloom/ui/task_review.py` | `e08877ab443882a2cb423fd8eed5816b82e8a00099c6efbecabc3285a8812195` |
| `src/linkloom/ui/static/product-task-review.js` | `8e2f4104bb8f043057b15b7de4fc34870bcb2cb2162dd92e041d351f90d1cbc4` |

Any later production change requires assessing its effect on this review.

## P1 found during this review — fixed in inspected code

**A fresh view capability could bypass the first PENDING request fingerprint
after proof capture succeeded but the receipt append failed.** The previous
check lived only inside the stale-capability recovery branch. Refreshing GET
issued a capability for the new proof head, so an altered actor/note/step with
the same request ID could pass the ordinary capability check and commit a new
statement. `fresh-capability-red.xml` objectively reproduces the `fresh_actor`
case as HTTP 200/COMMITTED before this fix. This is distinct from the four older
stale/foreign-capability tamper reproductions in `recovery-binding-red.xml`.

The final code now reads audit identity by `(workspace_id, request_id)`
(`task_review.py:480`) and, when no receipt exists, compares the incoming payload
against the **first** PENDING `details_hash` before either ordinary capability
validation or recovery (`task_review.py:1684-1695`). The fingerprint includes
Workspace, run, step, action, request ID, revision, actor, note, outcome, source
snapshot/catalog, pair and the original proof head. It reuses the existing
column and adds no business state or permission grant. Thus a new capability
cannot change the request, and a pending ID cannot be silently reused by a
different run in the same Workspace.

The fallback additionally requires a live capability with the same
Workspace/run/source/catalog/pair, the original revision/head and a matching
failed/current proof head. Receipt-present replay still checks the complete
stored payload fingerprint and refuses generic successful CONTINUE fall-through.
The new eight-case tamper test covers foreign capability, actor/note/step,
fresh-capability actor/note/step and fresh run, then requires the untouched
original request to recover without duplicate proof. **Final execution passed:
`final-recovery-suite.xml` contains all eight tamper cases plus recovery,
commit-boundary and cache coverage: 17 tests, 0 failures/errors/skips, 235.123
seconds.** The production SHA256 still matches the reviewed frozen code above.

## Seven safety checks

| Boundary | Result and evidence |
|---|---|
| Human review versus business approval | PASS by code. REVIEW/WITHDRAW only append self-asserted journal events; every view retains `business_approval=false`. UI labels and CLI help disclose unauthenticated actor labels and no business authority. Direct CHECKED does not enter proof assessment. |
| Receipt scope | PASS by final code and final 17-case recovery suite. Journal/CAS and fingerprint bind actor, Workspace, exact source/catalog, directed endpoints/pair and request identity. API rejects verdict/assessment/source/path overrides. Paths are Host-owned and shared with Runtime. |
| Canonical establishment only | PASS. `_assess_steps` calls existing assessors; only SUPPORTED assessments/COMPUTABLE comparisons reach the existing Store. RelationQuery currentness/readback gates established projection; row existence, local CHECKED and related history cannot establish proof. |
| SAME_TASK, revision direction and time | PASS. Existing `assess_plan_revision(new, old)` revalidates identity, rejects opposite direction and retains counter/effective-date conflicts. `compare_revision(new, old)` replays the premise and requires TASK_DEADLINE, DAY precision, compatible explicit boundary and asserted deadline change. Those canonical files were not changed. |
| Four-day comparison | PASS in final frozen-tree Chromium replay `i-final-1025`. 2026-10-14 to 2026-10-18 represents a source-supported boundary difference only. UI/receipt/business flag cannot label it formal business approval or select the current approved plan. |
| Withdrawal, version, conflict, cross-Workspace and #24 history | PASS by code and focused existing evidence. Withdrawal does not call relation lifecycle; catalog-only revoke fails in `ProductTaskSources.validate_current` before cache fallback; source updates redact old text/notes; proof lifecycle re-read blocks stale/retracted rows. Foreign Workspace is rejected before releasing history. `git diff --exit-code -- src/linkloom/tools/task_relations.py` returned 0: #24 comes unchanged from master. |
| No cache/regex/enumeration/permission shortcut | PASS. Projection cache is limited to 32 entries and capabilities to 256, cleans expiry on issue and clears both on close. Cached runs still pass current source/catalog checks and RelationQuery. Regex additions validate bounded identifiers; existing semantic parsers/assessors are reused. New local action/outcome labels are journal operations, not duplicated business truth states. |

The proof/review stores are separate databases, not a cross-database ACID claim.
PENDING/FAILED audit, exact replay, proof-delta validation and proof-side
`BEGIN IMMEDIATE` protect the receipt boundary. Continuous COMMITTED-audit
failure and external RETRACTED/STALE events are explicitly exercised; the code
returns BLOCKED without a false successful receipt.

## Evidence status and limits

| Check | Status at review checkpoint |
|---|---|
| Original evidence preservation | PASS independently checked: all 287 manifest paths exist and SHA256 matches; 0 mismatches. |
| Broad offline regression | PASS: `REGRESSION_SUMMARY.json` reports 1,325 unique final passes, including the four fresh-capability/run additions, with no remaining failed case. Initial Windows directory/path failures remain in raw logs; short/system-temp reruns supply final outcomes. |
| Recovery delta | PASS: `final-recovery-suite.xml` records 17 tests, 0 failures/errors/skips, 235.123 seconds. Older four tamper RED cases, earlier green/rerun XML and the fresh-capability RED remain preserved. |
| JavaScript syntax/DOM | PASS: all 8 entries in `UI_FINAL.json` return status 0; new review module and existing TaskAnswer/unified/stale/first-use regressions are included. |
| Integrated Chromium | PASS: final serial `output/playwright/task-relation-review/i-final-1025/result.json` and `verification-summary.json` both report PASS after the final code freeze. Independently read counts: 213 HTTP responses, 10 review operations, 3 verified source versions, 80 exact quote checks, all source checks true, 0 page errors and 0 external requests. The 01–15 workflow screenshots exist. Seven console entries/expected negative HTTP outcomes remain visible; this is not a console-silence claim. Prior timeout attempts remain preserved. Main and default-Host HAR/trace files exist with nonzero sizes after browser closure. |
| Default Product Host | PASS: `i-final-1025/default-host/result.json` reports PASS on Chromium 155.0.8059.85 without local-review opt-in; the two original source versions, content SHA256 values and exact quotes are recorded. This confirms normal TaskAnswer availability and the disabled review boundary; it does not establish the ambiguous original relation. |
| #25 composition | PASS for patch/DOM only: `PR25_LATEST_COMPATIBILITY.json` records all 11 checks code 0 for latest inspected head `aa3473d18b5c4542b665a88df1cfbb7dcea88ed5`, based on master `f2ce6b82d635a7b78b17a6d100556c354f7a461a`. The older 13-check composition remains historical evidence. Neither result proves final combined Chromium. |
| Commit/push/Draft/CI | Pending and coordinator-owned. No merge authorization is inferred. |
| Real Provider / real vault / business approval | Not applicable to this synthetic/offline Draft boundary; not tested or claimed. |

## Retained limitations and next gate

Original P2 findings remain scoped out: UX02 saved-source FAILED presentation,
UX03 offline guidance/example mismatch, UX04 source navigation losing question/
result. The backend's direct CHECKED statement policy is wider than the UI's
SUPPORTED-only choice, but neither can upgrade proof. The approximately 90 KB
service still combines journal, projection and recovery concerns; a later
explicit structural change can extract replay/projection helpers. No unrelated
cleanup is required for this Draft.

Before a merge decision: complete head CI and the publication/evidence receipts,
review the full production diff, reconcile latest #25 hook/include/CI overlap,
then run the final combined bilingual/desktop/mobile Chromium acceptance. Keep
the state DRAFT_READY_PENDING_UI_INTEGRATION until that last boundary passes.

## Reflection

A capability proves permission for a particular current view; it does not make
an existing request ID mutable. PENDING must reserve the same Workspace-wide
request identity as the eventual receipt, and its fingerprint check must precede
every capability route. The red reproduction and the minimal final control-flow
change make that boundary reviewable without expanding this feature into a new
identity system or changing canonical task semantics.
