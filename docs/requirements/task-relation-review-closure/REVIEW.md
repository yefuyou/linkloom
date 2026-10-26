# Independent review — task relation review closure

Review date: 2026-10-25  
Reviewer role: read-only, independent backend/security reviewer  
Scope: frozen production diff and the submitted SPEC/implementation boundary. This review writes this document only; it does not change production code, run a Provider, touch a real vault, or repeat the 111-case core proof suite.

## Verdict

**PASS for P1 closure: no remaining P1 finding.** The final backend difference removes the unsafe CONTINUE fall-through and makes `source_proof_status` a post-RelationQuery projection. The fresh-capability, continuous-audit-failure, proof-head/lifecycle, workspace/catalog, and browser acceptance evidence now cover the previously missing boundaries. A few P2 contract/maintainability notes remain below, but they do not create a false `COMMITTED` result or an unguarded proof upgrade.

The original Qingzhu/林枫/2029 example correctly remains `UNKNOWN`/`BLOCKED`; a local human statement does not upgrade it. No business approval, authenticated identity, RBAC, or Provider-backed proof is implemented, and none is implied by this SPEC.

## P1 findings — resolved in the final backend diff

### P1-1 — Existing CONTINUE fallback can claim success after proof-head or audit state changes — RESOLVED

Location: `src/linkloom/ui/task_review.py:1574-1648`, especially `1640-1648`.

The new `repair_pending` and `replay_committed` branches correctly require the stored receipt proof head to equal the live binding proof head. However, when neither branch is selected, the code still accepts `prior_binding` (which rewrites the capability binding to the stored receipt's old proof head), then falls through to the generic block:

```text
if action == CONTINUE and existing.status == RECORDED:
    if not committed:
        try audit(COMMITTED)
        except Exception: pass
return {...}, 200
```

The final code replaces that fall-through with an explicit `409/BLOCKED` response at `task_review.py:1646-1657`. It no longer swallows a COMMITTED-audit failure or returns HTTP 200 without a top-level completion status. The proof-head mismatch is reported as a blocker, while the original PENDING capability remains the only recovery token and the exact committed replay returns `status=COMMITTED`.

Final evidence:

- `tests/integration/test_task_review_commit_boundary.py:14-87` injects continuous COMMITTED-audit failure, retries with a fresh view capability, verifies non-200, repairs with the original capability without duplicate proof, then rejects a post-commit lifecycle-head change. The same test asserts the revalidated proof is not current after the lifecycle event.
- The coordinator's final focused evidence reports the commit-boundary and related recovery cases green, including the short-path rerun. No `except: pass` remains on the existing CONTINUE response path.

The exact request retry contract intentionally remains capability-bound: a fresh capability is not a substitute for the original PENDING recovery capability. That is a P2 API-documentation consideration, not a false-success path.

### P1-2 — `source_proof_status` can remain CURRENT_CONFIRMED after RelationQuery invalidates the proof — RESOLVED

Locations: `src/linkloom/ui/task_review.py:878-880`, `:890-950`, `:999-1005`; `src/linkloom/ui/static/product-task-review.js:62`, `:109-118`.

`_refresh_relation_proofs()` still derives deterministic source diagnostics first (`task_review.py:878-880`), but `_get()` now re-derives `source_proof_status` from the final RelationQuery-backed `step["proof"]["status"]` at `task_review.py:1097-1103`. The UI may continue to prefer `source_proof_status` (`product-task-review.js:62`) because the field is now synchronized with the authoritative proof result.

The final lifecycle regression at `test_task_review_commit_boundary.py:64-87` verifies both fields become `BLOCKED` after an external lifecycle mutation. The final browser run also shows `CURRENT_CONFIRMED` after proof capture/withdrawal and clears it after source update/removal, while the separate `source_verdict` remains a diagnostic and never becomes business approval.

## What passed, and what the evidence actually proves

| Area | Result | Evidence / boundary |
|---|---|---|
| Review journal durability | PASS | `TaskReviewStore` uses an append-only SQLite sidecar, immutable triggers, unique `(workspace_id, request_id)`, payload fingerprints, `BEGIN IMMEDIATE`, and revision CAS (`task_review.py:41-365`). Unit coverage includes idempotent retry, altered retry collision, stale revision, cold journal read, and concurrent CAS. |
| Local authority boundary | PASS by design | GET/POST advertise `LOCAL_SELF_ASSERTED_ONLY`; `business_approval` is false; actor labels are free-text local assertions. REVIEW/WITHDRAW append review rows only and do not call the relation lifecycle. This is not authentication or business approval. |
| Fixed Host composition | PASS in focused host tests | `server.py:55-111` resolves a trusted explicit mapping or a database-adjacent default, never accepts a client path, shares the mapping with Runtime, and disables durable sidecar creation for `:memory:`. Empty-product, default-path, explicit-path, and memory/empty cases are covered. |
| Workspace/source/catalog boundary | PASS in focused integration tests | `task_review.py:798-820,1015-1085` checks the selected workspace and current sources/evidence; catalog-only evidence revoke and source version change redact old note/quote text; foreign workspace requests are rejected. `related_history()` filters exact workspace, run exclusion, source snapshot, source catalog, and pair hashes (`task_review.py:406-440`) and is read-only projection data, not CAS/proof input. |
| Proof capture and readback | PASS | `_continue()` uses PENDING → original `TaskRelationStore` capture → `RelationQuery` readback → proof-side `BEGIN IMMEDIATE` → review receipt + COMMITTED audit (`task_review.py:1767-1862`). The final boundary tests cover receipt/audit failure, fresh-capability rejection, external lifecycle races, exact proof counts, and successful recovery without duplicate proof. |
| Source/proof separation | PASS | `_get()` re-derives `source_proof_status` after RelationQuery (`task_review.py:1097-1103`). `source_verdict=SUPPORTED` remains a deterministic source diagnostic and is visibly separate from `proof.status`; neither a source verdict nor a local statement establishes P4 proof or business approval. |
| Related history | PASS within its stated scope | Exact old-run history is excluded from the current run and cannot participate in revision/CAS/proof. It is redacted when current source/catalog validation fails. |
| UI rendering | PASS | `tests/ui/task_review_dom_test.cjs` covers escaped bounded evidence, separate source/proof labels, local-only forms, withdrawal, history redaction, and CHECKED disabled for a non-SUPPORTED source verdict. Final Chromium screenshots cover proof receipt, reload, cold Host restart, withdrawal, source update, conflict evidence, and source removal. |
| PR24/currentness isolation | PASS | `git diff -- src/linkloom/tools/task_relations.py` is empty in this worktree; no PR24 currentness repair was copied into this task. |

Coordinator-provided focused evidence reports 13 PASS plus a seven-case short-path rerun PASS after Windows artifact-path failures. The initial loopback `WinError 10013` run remains an environment failure and is not silently reclassified as a product pass. The added commit-boundary cases cover the two previously missing replay/projection boundaries; `evidence/commit-boundary-final.xml` independently records both boundary cases PASS (2 tests, 26.611 seconds).

## Existing proof contract retained

The service delegates to the existing canonical functions in `task_review.py:1345-1401`:

- `SAME_TASK` calls `assess_same_task(subject, object, ...)` with current workspace artifacts/registry. It is a fixed-pair identity assessment and stays `UNKNOWN` when identity/scope is insufficient; a human statement cannot supply the missing identity evidence.
- `REVISES_PLAN` calls `assess_plan_revision(new, old, same_task=identity, ...)` (`task_plan_revision.py:176-205`). The subject is the later/new plan and the object is the earlier/old plan; a supported SAME_TASK premise is required.
- Date comparison calls `compare_revision(new, old, ...)` (`task_revision_comparison.py:149-192`). It replays the revision proof first, then requires both endpoints to have a `TASK_DEADLINE` with `DAY` precision and values, requires compatible explicit boundary semantics, and computes `new_date - old_date`. A date difference alone is not a revision proof.
- `RelationQuery` is the establishment gate: a relation is current only when all lifecycle, evidence, source-context, premise, and replay checks are clear and the canonical result is `current_supported=True`/`CURRENT_CONFIRMED` (`task_relation_query.py:185-228`). A row's existence or `ACTIVE` lifecycle, `source_verdict`, local CHECKED statement, or historical related receipt is not ESTABLISHED.

The original ambiguous demo therefore remains a valid negative case. A positive D04-style UI import must use the actual source IDs assigned by that import and must create proof through CONTINUE; this review does not claim a real Provider or a pre-seeded P4 result.

## Final acceptance evidence

- `output/playwright/task-relation-review/final-20261025-2005/result.json` reports `status=PASS`; this is the primary final Chromium artifact after the backend freeze. It contains the original and positive HAR/trace artifacts, HTTP/console/action logs, and screenshots 01–15. `final-20261025-1958` remains a historical full-PASS run, not the primary post-freeze index.
- `output/playwright/task-relation-review/final-20261025-2005/verification-summary.json` reports `status=PASS`: 146 HTTP responses, 10 local review operations, 3 verified source versions, 80 exact quote checks, and all source checks passing. It records 0 page errors and 0 external requests. The 7 console entries are retained as evidence: 6 expected negative 409 responses plus 1 empty-workspace bootstrap `review/candidates` 409; this is not a claim of console silence.
- The real Chromium path created A/B sources through the UI, ran CONTINUE with the dynamically assigned source IDs, captured canonical proof, produced the four-day date comparison, created a fresh Runtime run with exact related history, reloaded the page, and read the same run after a cold Host restart.
- The same run verified that local withdrawal preserved source proof and did not emit a relation lifecycle event; a second browser's source update cleared the old conclusion; conflict support/counter evidence was shown; and removing a source cleared the old evidence. Cross-workspace access returned refusal without task data.
- The ordinary Product Host acceptance at `output/playwright/task-relation-review/final-20261025-2005/default-host/result.json` also reports `status=PASS` without the local-review flag: TaskAnswer remains available, the original demo's two source SHA/quote pairs were inspected and matched, and three PNGs plus the browser HAR/trace artifacts are retained under that `default-host` directory. The disabled Host correctly reports no local review capability and no business approval.
- The earlier `final-20261025-1950` wording is corrected here: source import itself succeeded. Its harness waited for a response from `/api/sources` while the product operation used `/api/sources/import`; the visible `FAILED` label was the known UX02/P2 ingestion-status presentation issue, not evidence that the import endpoint failed. `final-20261025-1955` reached 12 PASS but stopped on a harness scope-refresh 409; `final-20261025-1958` added the required refresh and completed the full path without a production change.
- This is an offline transport/source fixture acceptance. No real Provider, API key, real vault, or pre-seeded P4 proof is claimed. The original Qingzhu demo remains UNKNOWN/blocked in screenshots 01–03 and the `original_blocked` result.
- No authenticated human identity, RBAC, business approver, or business-state transition exists. This is an explicit non-goal in SPEC §9/§19, not a missing implementation to be papered over by `actor_label`.

## Residual P2 notes (not P1 blockers)

1. An exact CONTINUE recovery retry remains capability-bound. A fresh capability is intentionally rejected for a pending/committed replay rather than silently treating it as the original proof-capture capability. If a future UI contract requires replay after a page refresh with a newly issued capability, specify and test that separately.
2. The backend accepts a direct `CHECKED` POST for any valid step outcome while the UI disables CHECKED when the source verdict is not `SUPPORTED`. This cannot upgrade RelationQuery proof and is therefore not a P1 bypass, but the API/UI policy should be made explicit or enforced server-side in a later scoped change.
3. `task_review.py` remains a large feature module (Store, capability, projection, proof capture, recovery, and service). The final state is reviewable and the P1 control-flow defects are closed; extract a single replay/repair helper and projection helper as a separate maintainability change rather than widening this milestone.

## Architecture note

`src/linkloom/ui/task_review.py` is approximately 1,894 lines and combines the SQLite Store, capability issuer, projection/rebuild logic, proof capture/readback, recovery state machine, and HTTP-facing service. File size alone is not this review's blocker, but the resolved P1 findings exposed duplicated recovery conditions and cross-layer state projection in the same control flow. The smallest safe follow-up is to extract a single replay/repair decision helper and a single post-readback projection helper; a larger Store/service split can follow separately. Do not widen this task into a general UI cleanup or copy PR24.

