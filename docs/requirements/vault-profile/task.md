# Task Ledger: Vault Profile

Status: Planning draft. Implementation is not approved.

## Current Milestone And Feature

- Milestone: 1 - Can Read.
- Feature: Vault Profile.
- Active slice: planning-only read-only profile over an existing Scanner index.

## Completed Work

- [x] Product problem and user outcome defined.
- [x] Input and output contracts defined.
- [x] Read-only, synthetic-only, and no-model boundaries defined.
- [x] Determinism, immutability, and failure acceptance cases defined.
- [x] Proposed implementation file boundary defined.
- [ ] Independent planning review passed.
- [ ] Human approved the feature SPEC and implementation plan.
- [ ] Worker implementation started.

## Fixed Decisions

- [x] Vault Profile consumes `vault_index.json`; it does not scan a vault.
- [x] It reads no original Markdown note after receiving the index.
- [x] It reports observations and fixed unknowns, not semantic judgments.
- [x] It creates only profile artifacts in an explicit output directory.
- [x] It uses Scanner schema version 1 and rejects unknown schema versions.
- [x] It does not use a model, network, provider key, relation experiment, or
  real vault.
- [x] It is a Milestone 1 closure slice, not a new milestone.

## Current Task

Obtain an independent planning review and human approval. Do not create
`src/linkloom/profile.py`, modify `cli.py`, or add tests until that gate passes.

## Planned Acceptance Evidence

- a Scanner-generated synthetic index;
- a copyable profile CLI command;
- profile JSON and Markdown artifacts;
- byte-identical repeat artifacts;
- input-index before/after byte comparison;
- invalid-index rejection with no artifacts;
- visible limitations section;
- declaration of no real-vault, network, model, or Git commit use.

## Learning Points

- Derived artifacts let later features use a trusted index without repeatedly
  touching source notes.
- Deterministic aggregation makes every reported count inspectable and stable.

## Interview Evidence

- a staged ingestion-to-profile flow;
- tests for schema validation, determinism, and immutability;
- a concrete example of refusing to turn structural counts into AI judgments.

## Remaining Issues

- The Scanner index is intentionally structural. It cannot answer why a folder,
  tag, or missing link exists.
- Semantic search, relation discovery, health diagnosis, and action extraction
  remain later milestones.

## Next Candidate Task

Independent review of this feature plan. After human approval, implement only
the approved Profile file boundary.
