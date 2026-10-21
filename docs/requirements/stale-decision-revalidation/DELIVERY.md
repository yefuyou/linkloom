# STALE Decision Revalidation delivery

Status: **IMPLEMENTED / DRAFT ONLY — BLOCKED_BROWSER_ACCEPTANCE**.
Date: 2026-10-21. Baseline: remote master
`ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`.
Branch: `codex/linkloom-v1.1-stale-revalidation` in an independent worktree.

## User flow

1. Open Review → Decision records needing attention → Revalidate decision.
2. Compare the original decision, immutable quote/version and authorization
   receipts against current source text and the exact missing/changed evidence.
3. Keep unresolved returns to Review and grants nothing. Refresh the comparison
   after ingestion or source changes.
4. Choose Review this new candidate for the same workspace and canonical slot.
   The existing review form shows the new evidence, reviewer and reason, and
   can resolve an unstated effective time. Approve and materialize requires a
   fresh, candidate-specific human approval and unchanged review snapshot.
5. Successful materialization closes that attention record. The original stays
   STALE, with its original dates/evidence/receipts and an explicit revalidation
   relationship to the new receipt. History shows both records read-only.
6. If materialization failed after approval was recorded, Finish recorded
   approval continues only that exact target/candidate approval. Its actor,
   reason and resolution are shown read-only. New reviewer/resolution fields
   are rejected for this retry; no second authorization is created.

The relationship is an audit revalidation, not a fabricated source-asserted
business replacement. No old valid-time boundary or supersession ID is rewritten.

## Reproduce locally without keys

From the repository root, install the normal test dependencies:

```sh
python -m pip install -e '.[dev]'
python -m scripts.demo_stale_revalidation --directory work/revalidation-demo-1
```

Use a fresh directory each time. The script prints its localhost URL, prepares
one synthetic workspace, records an initial approval, updates the source and
leaves the new candidate pending. It injects the existing scripted fake model
through ProviderNeutralSemanticExtractor; it does not read keys or call Gemini.
Open the printed URL and follow the user flow above.

Optional automated real-browser acceptance is prepared but **not executed**:
`tests/ui/stale_revalidation_browser.cjs`. It starts its own fresh fake-only
HTTP/SQLite demo, launches a new Chromium context, exercises Keep unresolved
and explicit approval, checks history and cleared Attention, and writes five
before/comparison/approval/after PNGs plus `browser-result.json`.
It requires Playwright and its Chromium binary. With that dependency installed:

```sh
node tests/ui/stale_revalidation_browser.cjs work/revalidation-browser-1
```

Set `LINKLOOM_TEST_PYTHON` if the test Python executable is not `python`.
The real-browser script is syntax-checked only; its runtime results and
screenshots must not be assumed until it has run successfully.

## Objective checks

| Check | Result |
| --- | --- |
| Current offline Core workflow, including the new HTTP suite | 287 passed, 2 legacy tests deselected |
| New real local HTTP + SQLite suite, rerun after final changes | 17 passed |
| Product application + frozen ledger protection + original historical input compatibility | 29 passed |
| Independent final memory/reconciliation/materializer + new feature tests | 70 passed |
| Existing Ask DOM, new revalidation DOM, JS syntax | PASS |
| Wheel build and git diff --check | PASS |
| Actual browser UI journey and before/after screenshots | BLOCKED, not passed |

The count rows overlap and must not be added into a combined test total.
The new HTTP and DOM tests are part of the existing offline CI workflow.

Covered HTTP cases include fresh authorization, original evidence/receipt
preservation, no side effects on read/leave, stale snapshot/fingerprint,
workspace changes, rejected/proposal/future/unrelated-slot candidates, source
removal, source rollback, restart, and target-bound materialization recovery.
The missing-source catalog case uses a scoped test double behind the real HTTP
route; SQLite foreign-key integrity is not disabled. Independent fault tests
also rejected a resume using a different stale target and a generic ordinary
APPROVED candidate.

## Browser block and screenshots

The connected Browser tool reported that it lacked the originating context.
The documented cloud-browser fallback was available, but could not reach the
synthetic server: localhost returned `ERR_CONNECTION_REFUSED`. Inspection
confirmed each execution command has its own network namespace, so its local
HTTP server is unreachable from the browser environment. A disposable,
synthetic-only relay did not resolve this; it is not part of this branch and
was stopped. Production localhost and Origin checks remain unchanged.

No actual product before/after screenshots were obtained. DOM tests and the
prepared browser script are not substitutes for that acceptance evidence.
The branch must remain Draft until live browser acceptance is completed.

## Remaining boundaries

- Source updates/removals are explicit product operations, not OS watchers.
- Revalidation requires an already resolved subject/relation matching the same
  canonical slot. It does not infer identity repairs, cross-slot equivalence,
  missing source documents or new evidence.
- A source rollback cannot revive the completed original approval. If the exact
  historical artifact/candidate identity is reused, it stays stale; create a
  distinct current-source candidate and review it rather than reusing that ID.
- No source-asserted business supersession is invented. Historical stale
  records are audit-only, not verified historical truth for Ask.
- Workspace isolation is the existing local product contract, not enterprise
  identity/RBAC. Source removal preserves history for that workspace's audit.
- No real model quality, real-provider compatibility, production deployment,
  visual usability or browser success is claimed by the offline results.

No Runtime, M3-P, frozen evaluation/Gold/Seal or real Provider defaults changed.
No real vault or private source was used. No schema or parallel approval system
was added, and master was not merged.
