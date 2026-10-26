# PR26 final combination acceptance with PR25

Status: local combined acceptance passed; remote publication and exact-head CI
are read back in the PR and local PUBLICATION.json. Human merge remains required.

## Integration and conflicts

- Latest verified master: `22a7b1a01ac03ec6b356357ea51ed0d6e68c5bd0` (PR25 merged).
- Original PR26: `3acf346918abb32200291cf743202e6447f4bc45`.
- Original worktree/branch retained; normal `merge --no-commit --no-ff origin/master`.
  Zero textual merge conflicts; no rebase/reset/force push or history rewriting.
- Automatic merge preserved the post-render hook in product.js, the deferred
  task-result -> task-review -> product script order, and the union of both CI
  registrations (five PR25 DOM suites plus task-review DOM/backend checks).
- product-task-result.js, product.css, i18n.js, legacy static files and all PR25
  documents remain identical to master. Review backend, task-review module,
  server/CLI and #24 task_relations.py remain identical to original PR26.
- One minimal compatibility repair: global interface-language changes re-read
  the displayed accepted task via existing GET, hide old evidence while waiting,
  preserve saved history and source-proof state, and never replay the investigation
  POST. One bounded workspace/run pointer also protects overlapping language
  changes; navigation/invalidation/new submission clear it. Existing view-token,
  Workspace and locale guards reject late responses. No new cache, timer, event
  binding, semantic regex, provider dependency or business authority.
- Focused RED/GREEN and overlapping-language/source-revocation regressions are
  appended to unified_ask_dom_test.cjs; no original assertion removed or weakened.

## Local regression evidence

[Summary](regression-summary.json): **1329 unique cases finally PASS**, 0 final
failures, 0 skips. This runs every Python group in the merged offline workflow,
including Product HTTP, source authorization, Grounding, F/G, task relations,
review/recovery/CAS, #24 currentness, frozen bytes and legacy inputs. Six DOM
suites and three JavaScript syntax gates also pass on final production source.
The review group contributes 30 tests; frozen ledger 5; #24 currentness 7.

Original logs remain in `work/task-relation-review/final-combination-20261026/`.
Initial failures are preserved and classified:

- One existing localhost HTTP timeout (`timeout=3`) during the large run;
  unchanged isolated retry passed ([JUnit](timeout-retry.xml)).
- Offline entry and early copy-browser roots exceeded Windows path capacity,
  producing `artifact_write_failed`. Identical contracts pass on short system
  temporary synthetic roots. No source/proof/response or assertion was replaced.
- Copy harness: system Node18 rejected, POSIX PYTHONPATH selected an unrelated
  editable install, and Linux single-process flags crashed Windows Chrome.
  [Windows adapter](windows_browser_env.cjs) uses bundled Node/Chrome, correct
  PYTHONPATH and normal multi-process launch. It also lets a historical-date
  recovery GET finish before clicking Ask, and allows the original asynchronous
  Runtime wait. All **33 original browser steps pass**; failed attempts remain.
- A new wrapper initially expected an external-provider checkbox in explicit
  offline mode; that checkbox is absent. Final check rejects checked external
  permission and permits absence; no existing test assertion changed.
- Early opt-out browser accidentally overlapped owned host teardown. Final
  opt-out run uses fresh data and separate port 8793; HTTP write is rejected 403.

`git diff --check` for new work passes. Whole-PR whitespace diagnostics retain
**44 stdout lines identical to original PR26** in immutable old reports/RED logs;
[comparison](whitespace-baseline.json). Those materials were not rewritten.
663 protected original evidence/fixture/ledger files have unchanged SHA256
([preservation](preservation-result.json)); PR25 source/report files are preserved.

## Frozen real Chromium evidence

The [reproducible journey](browser_acceptance.py) runs actual Product Host HTTP,
UI form imports and operations with scripted offline model transport, no seeded
P4 proof. It keeps the original acceptance script byte-identical. Full raw HTTP,
Console, HAR, Trace, source inputs, DOM text and screenshots are local at:

`output/playwright/task-relation-review/combined-2526-1026-frozen/`

[Verification](browser-verification.json): **159 API HTTP responses,
13 review POSTs, 3 source versions, 80 exact
source/quote SHA checks; 0 page errors, 0 external requests**. The 8 Console
resource errors match 7 expected rejected 409 adversarial checks plus one
WORKSPACE_REQUIRED response while the new Host has no selected workspace;
there are no uncaught JavaScript failures. HAR/Trace include ephemeral capabilities and stay
local; committed screenshots are byte-identical copies of synthetic UI captures.

The full user chain is reproducible: open -> create Workspace -> import A/B
synthetic meetings -> investigate SAME_TASK/REVISES_PLAN -> expand original and
counter-evidence -> persist local statement -> independently recheck source proof
-> new investigation -> 2026-10-14 to 2026-10-18, difference 4 -> refresh -> owned
service restart and readback. Desktop 1440x1000 and mobile 390x844 are checked;
mobile English save, answer-language toggles and both global interface languages
retain persisted history/date evidence. All captured pages have one h1 and no
horizontal overflow. Result/source/next-step hierarchy still precedes diagnostics
and the added review panel.

Negatives: original ambiguous same-task proof stays blocked; support plus explicit
counter-evidence remains CONFLICT; withdrawal retains independent proof and audit;
source version update invalidates old evidence; Workspace source removal revokes
access; foreign Workspace shows no old task data; exact duplicate is idempotent,
altered duplicate is 409, two concurrent requests yield one 200 and one 409.
Catalog-only authorization revocation, pending receipt recovery, source SHA,
lifecycle and date direction/role boundaries retain their Python regressions.

[Opt-out write receipt](disabled-write.json): fresh final-source Host without
`--allow-local-task-review` returns 403 LOCAL_TASK_REVIEW_DISABLED and retains the
ordinary task answer; raw HAR/Trace/HTTP/source verification is at
`output/playwright/task-relation-review/combined-disabled-1026-final/`.

[PR25 browser summary](copy-browser-summary.json): 33 original steps PASS;
raw screenshots and responses in
`output/playwright/task-relation-review/combined-copy-1026-g1/`.

## Trust boundary and remaining limits

Actor labels remain self-asserted local identities, never authenticated approvers.
Review receipts never mean business approval. Only canonical independently
revalidated SAME_TASK and new->old REVISES_PLAN plus date evidence create trusted
proof; UNKNOWN/CONFLICT cannot be upgraded by clicking. Four days is only a
source-supported date-boundary comparison, not confirmed formal plan status.
Source/catalog/authorization/Workspace/lifecycle changes revalidate; #24 historical
currentness is preserved. Local opt-in stays explicit. No real Provider/Key,
external writes, real vault data, Gold, Canary or historical score edits occurred.

Original P2 UX02/UX03/UX04 remain outside this patch: underlying saved-source
FAILED state, offline demo mismatch, and navigation losing question/result were
not repaired. PR25 already relabels the saved-source display; its original raw
FAILED value remains. Unsaved task-review form drafts can reset when language
rerenders the panel; saved statements/history are preserved. This existing draft
behavior is not expanded into new draft-persistence functionality here.

Full production diff is preserved locally at
`work/task-relation-review/final-combination-20261026/production.diff` and can be
regenerated with `git diff 22a7b1a01ac03ec6b356357ea51ed0d6e68c5bd0 HEAD -- src`.
The final PR Head/Tree and GitHub preview integration Tree must match. Remote CI
is only claimed after exact-head SUCCESS readback. Keep Draft; do not auto-merge.

Reflection: an automatic merge and separate passing feature suites did not prove
the UI combination. The final source is accepted only with actual combined-tree
browser/HTTP evidence and current-source locale revalidation.
