# SPEC: accepted P1 integration and independent Draft submission

Boundary: user's 2026-10-25 follow-up authorizes integrating the accepted task
review patch with actual latest master, necessary regressions, normal commit
and push, a separate Draft PR targeting master, and CI readback. This supersedes
only the previous no-publication boundary. It does not authorize merging into
master, force push, history rewriting, PR25 mutation, or new business features.

Base fetched and verified: f2ce6b82d635a7b78b17a6d100556c354f7a461a.
PR25 inspected head: 3d4c05025c64aed0f51a15b6aa7d476febc972f8, still Draft.
During execution it advanced to aa3473d18b5c4542b665a88df1cfbb7dcea88ed5;
the latest patch/DOM composition was rechecked separately without merging it.

The original feature SPEC, delivery, independent review and all captured browser
evidence remain immutable. Their prior conclusions describe the prior accepted
tree; integration outcomes are appended only in this directory and a new
browser output label. PRE_INTEGRATION_MANIFEST.json binds those old files.

No redesign of human review, relation persistence, semantic predicates, current
business state, or revision/date contracts. The only newly necessary production
guard is bounded process memory for cached run projections and issued local
capabilities, discovered by the requested cache audit. Expiry/capacity eviction
must fail closed and must not rewrite durable review/proof/audit state.

The requested authorization audit also exposed a pending recovery branch that
did not require the original view scope or statement fingerprint. Tighten that
existing branch to the original live capability, Workspace/run/source/pair,
actor/note/step request identity and PENDING fingerprint. Reuse audit details_hash;
no schema, relation semantics, permission grant or business state is added.

Acceptance: original P1 browser workflow on the integrated tree; #24 historical
negative/currentness protections; relevant offline Core, Product, UI, frozen
integrity and new review regressions; independent read-only review; preserved
original evidence hashes; exact source/tree/remote head and CI results.

Keep PR25 separate. Explicit patch/DOM composition may document technical
compatibility, but does not substitute for final combined Chromium acceptance.
Final status remains DRAFT_READY_PENDING_UI_INTEGRATION until that separately
authorized combined UI is validated. No real Provider, Key, Gold/experiment
mutation, real vault, production data or authenticated business approval.

Execution: preserve -> fetch/fast-forward only this branch -> minimal memory
guard and CI test registration -> local regression/browser -> independent
review -> exact staged scope/commit -> ordinary push -> Draft/attach -> CI
readback. Root coordinates/integrates; independent reviewer writes REVIEW.md
only and does not repair production code.
