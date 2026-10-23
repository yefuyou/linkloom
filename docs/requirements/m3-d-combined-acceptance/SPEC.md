# Integration D: M3-S1 + M3-S2

The user's Integration D request approves this bounded integration and its
acceptance work. Base: `bde9d9e2b88c2e69132cf48d1534dc28d4fca764`;
S1: `4157dae1fb9331bb95888eff3ec331aa0b612459`;
S2: `1d3a3363757e00bf8c9970564ad2bfb99fa27fbd`.
Branch: `codex/m3s1-s2-combined-acceptance`, independent worktree.

Use a normal two-parent merge of the complete S2 history from fixed S1. Preserve
both original branches, evidence, tests and budgets. No squash/rebase/reset or
force push. No PR, master merge or upstream branch update.

Architecture remains the existing Product HTTP -> authorized source snapshot ->
F v3 TaskObservation -> G bounded planning/replanning -> Runtime -> Reviewer ->
saved result/cold revalidation. F's versioned recall/date protocol and G's
source-bound reference projection are complementary changes to that boundary.
No new dependency, provider, permission or business authority is introduced.

Acceptance scenarios (all synthetic):

- A: explicit work with source-grounded owner and date token before a boundary
  word. F v3 fields survive G/Runtime and source verification.
- B: explicit work with all fields unknown remains an observation; independent
  inspection executes and Product honestly returns PARTIAL.
- C: discover adds no information. The complete offline HTTP trace must show an
  UNKNOWN receipt, one replan, the original IDs/dependencies/source handles in
  the bounded input, retained history, blocked dependent inspection and legal
  independent inspection. Replanning cannot re-enable discover or exceed the
  original 24,000-byte input bound. LIVE C is claimed only with actual Provider
  and Host branch evidence.

Regression covers UNKNOWN/CONFLICT premises, no discover completion claim,
unconfirmed relations/P4, old profiles/Frozen/Gold, source revocation/update,
Workspace isolation, provider-free cold reads, DOM and real Chromium.

Implementation/acceptance order: semantic merge inspection -> combined tests and
complete offline traces -> relevant regression/DOM/Chromium -> current ledger
reconciliation -> at most 12 new real requests/$3 durable reservation ->
independent read-only review -> normal commit/non-force push and remote readback.
Only gemini-3.8-flash, google-genai 2.22.0, low thinking and SDK attempts=1.
No automatic retry after FAILED/UNCERTAIN, no historical reservation release.
Account for other active allocations under the project $50 cap before sending.
If authorization/environment is unavailable, report LIVE blocked with zero new
paid requests. Historical/current results and overlapping test batches are
reported separately. No real vault is used.
