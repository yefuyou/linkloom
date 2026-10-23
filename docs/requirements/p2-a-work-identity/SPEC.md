# P2-A: same-message work observation identity

Approved boundary: the user's P2-A request, base
`33390e2eeaad71d215e279b2f724cae672ea4f80`, branch
`codex/m3d-fix-work-identity`. Only this identity defect is in scope.

Reproduce F -> identity collision -> Discovery quarantine -> empty G endpoints
-> Product UNKNOWN over normal HTTP using scripted transport, before changes.
Preserve BEFORE receipts and the baseline v1 payload/ref bytes as regression input.

New field-bound observations use a versioned identity from the immutable
workspace/version/canonical message/evidence and sorted, unique description
evidence offsets. Exact source verification, unique occurrence and containment
in the raw fragment are required. Candidate order, field roles/values, ingestion
time and raw-window size are not work identity. The proposal wire contract and
F prompts remain unchanged. Discovery enables v2 for the current TASK_RECALL_V3
profile; legacy V1/FIELD_GROUNDED_V2 profiles explicitly retain v1. Direct new
field-bound construction defaults to v2. Saved v1
observations (including field-bound records) replay the exact old formula and
extraction. ID prefix selects strict validation. No fallback accepts arbitrary IDs.

No aliases, P4 migration, relation rewriting or inherited authority: new refs
must pass the existing P4 proof and payload-hash contracts independently. Historic
v1 observations remain one original observation on read. ObservationRef and
DiscoveryGroup are bindings/proposals, never business-task proof.

Discovery quarantines only inconsistent candidates with the same work identity;
other work in the same raw range survives. Duplicate candidates do not create
extra observations. Fields with missing/uncertain evidence remain UNKNOWN.

Acceptance: independent tests for shared owner/date/raw, order reversal, partial
fields, duplicate work, conflicting roles plus a surviving independent work,
strict v1/v2 replay and tamper rejection, baseline stored v1 compatibility, P4
noninheritance, current-source/permission/workspace hot and cold fail-closed,
full F/G/Runtime/Reviewer/HTTP/cold read. Run the affected regression suites and
independent read-only review for stability, compatibility, grounding and duplicates.

No paid models, prompt changes, G replanning changes, business regex, Gold/Frozen
changes, unrelated UI changes or PR changes. Authorized normal commit and push
on this new branch only; no force push or merge.
