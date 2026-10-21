# STALE Decision Revalidation SPEC

Authorized boundary: the user's 2026-10-21 V1.1 request. Baseline master
`ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`.

From Review Attention a user can compare a stale approved decision's immutable
original evidence/authorization with current source versions and exact reasons
for lost validity. The user may leave it unresolved, or select a current-source DECISION candidate for the same workspace and decision slot,
review its evidence, resolve effective time if needed in the existing form,
and explicitly approve it via the existing lifecycle. Subject and relation must
already identify the same canonical slot; fields must be resolved before approval.

A revalidation is an audit relationship, not a source-asserted business
supersession. Original decision, dates, evidence and receipts remain unchanged.
The new human approval event records the original decision ID. Only its durable
materialization receipt closes Attention; failed approvals never close it.
No new approval framework or schema is introduced. New source updates still
invalidate the new decision normally. Historical stale records remain visible
in a read-only history, never presented as verified current truth.

Acceptance:
- Real HTTP + SQLite + browser before/after on synthetic timestamped sources.
- Leaving/reloading preserves unresolved state without granting authority.
- Explicit fresh approval creates a new receipt and preserves the original.
- Reject old snapshot/fingerprint, old candidate/authorization, unrelated slots,
  cross-workspace IDs, removed sources/evidence, future candidates, and approval
  attempts that leave required fields unresolved.
- No current valid candidate gives an actionable unresolved state.
- Existing review policy, grounding and source checks remain in force.
- Restart preserves relationship, receipts and historical evidence.

Out of scope: Runtime, M3-P, frozen evaluation, real provider defaults, real data,
OS file watching, arbitrary claim repair, auto-selection/auto-approval.
