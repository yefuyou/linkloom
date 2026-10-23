# M3-D Answer-first product integration

The user's 2026-10-22 integration request approves this boundary and the
implementation/check order below. Base: `33390e2eeaad71d215e279b2f724cae672ea4f80`.
UI: `94e52027fb97bde0d28fb003c7bab189975fb047`, exactly one commit after M3-S
`bde9d9e2b88c2e69132cf48d1534dc28d4fca764`. New branch:
`codex/m3d-answer-first-integration`, independent worktree.

## Architecture and scope

Normal cherry-pick the UI onto the fixed M3-D combination. Retain Product HTTP
and current-source authorization -> F task-recall/v3 -> G bounded replan ->
Runtime -> Reviewer -> TaskResult -> Answer-first renderer. The server continues
to own evidence validation, revalidation, permissions and business authority.
The renderer owns presentation only. No new dependency, paid Provider request,
real vault, business approval or architecture redesign is authorized.

## Acceptance and implementation order

1. Verify UI ancestry, one functional increment and clean cherry-pick. Inspect
   renderer, Product events, static route, HTML and CSS against M3-D.
2. Run Answer-first HTTP/DOM plus existing unified Ask, first-use and stale
   revalidation DOM. Select relevant M3-D, field/result, permission/P4 regressions.
3. Run actual Chromium against new synthetic HTTP hosts. Cover M3-D A known
   fields, B unknown fields, C UNKNOWN discovery with one bounded replan and real
   Runtime/Reviewer; also PARTIAL/model SUPPORTED, UNKNOWN, valid-citation
   CONFLICT and existing P4 date comparison. Save fresh screenshots, console
   events, exact source hash/version/offset checks and key HTTP receipts.
4. Reopen after refresh and with no Provider configured; exercise evidence
   revocation, source update and Workspace 409. BLOCKED must hide all old quotes,
   fields and model proposals. Desktop/mobile must not overflow. Reviewer
   acceptance and checked citations never grant business approval.
5. Independent read-only P1/P2 review. Fix only demonstrated compatibility
   defects, preserving failing reproduction, minimal patch and affected checks.
6. Commit evidence, non-force push the new branch and create a Draft Stacked PR
   targeting `codex/m3s1-s2-combined-acceptance`. Keep that target until PR #13
   merges; no Ready, merge, force push or original branch/master modifications.

Historical UI/M3-D evidence stays historical. This campaign produces new records;
offline scripted semantics are not claimed as a new real-model evaluation.
