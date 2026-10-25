# Product copy and information hierarchy SPEC

Authorized scope: the user's 2026-10-25 implementation brief. Baseline master:
`7720390a796662ff42c27b5eef6f8685e5155802`. Branch: `codex/product-copy-ux-cleanup`.

Improve product-facing wording and hierarchy across Workspace, Sources, Review,
Decision Memory, Ask, task/source investigation, legacy replay and current public
documentation. Use one page title, answers before diagnostics, contextual helper
text and equivalent Chinese/English terminology. Preserve typed state, provenance,
approval, currentness, external-source permission and cost disclosures.

Implementation: inventory rendered states and text; simplify presentation helpers
and result DOM; localize product labels; update current documentation; verify
existing DOM, Python/HTTP and synthetic Chromium desktop/mobile flows. Record
baseline and candidate evidence, exact change counts and unresolved checks.

No core Agent, F/G, Runtime, Reviewer, P4 or authorization rule changes; no real
Provider, keys, private data, frozen results or other branches. Submit and push this
branch only and create a Draft PR. Independent review reports findings without edits.


Continuation: keep existing Draft PR #25 and preserve remote commit 3d4c050 by
normal merge. Integrate current master f2ce6b8 without changing its core rules.
Retain prior receipts and DOM CI gate. Publish only an independently reviewed,
retested tree, using a non-forced expected-head branch update.
