# Vault Steward SPEC

## Status

Draft v0.1. This SPEC defines the project before implementation.

`SPEC` is the canonical term for project requirements, scope, acceptance
criteria, and phase gates.

## Mission

Build a local-first steward for Markdown knowledge bases. The steward should help
users diagnose, search, explain, reorganize, and safely maintain notes after
long-term accumulation or migration from other systems.

The project must be useful beyond one private vault. Personal vaults are dogfood
and case studies, not hard-coded product assumptions.

## Product Problem

Markdown and Obsidian-style knowledge bases drift over time:

- notes become isolated;
- topic pages become flat lists instead of navigable maps;
- old imported notes duplicate or conflict with newer wiki pages;
- migration can create empty pages or content-loss suspects;
- users need concept-level retrieval, not only folder-level browsing;
- LLMs can help organize notes, but direct mutation is dangerous.

## Non-Goals

- Do not silently rewrite user notes.
- Do not delete notes automatically.
- Do not mutate a real vault without reviewed dry-run output and explicit human
  approval for the exact target path and operation.
- Do not hard-code `D:\programblog` or any personal folder layout into core logic.
- Do not depend on a live Obsidian plugin for the core workflow.
- Do not start with a complex UI, background job system, or multi-host runtime.

## Phase Plan

### Phase 0: Governance

Goal: establish SPEC-first, multi-agent development rules.

Deliverables:

- `SPEC.md`
- `AGENTS.md`
- multi-agent role contract
- acceptance checklist
- reverse-engineered development patterns from reference projects

### Phase 1: Read-Only Vault Intelligence

Goal: understand a vault without changing it.

Planned tools:

- vault scanner;
- metadata extractor;
- link and tag indexer;
- empty/short page detector;
- duplicate title detector;
- report generator.

Output examples:

- `vault_index.json`
- `vault_health_report.md`
- `integrity_audit.md`

### Phase 2: Evidence-Backed Planning

Goal: propose improvements without applying them.

Planned workflows:

- taxonomy suggestion;
- concept-level retrieval;
- content integrity recovery plan;
- merge candidate plan;
- cross-link suggestion.

Every suggestion must include evidence paths.

### Phase 3: Permissioned Mutation

Goal: apply approved changes safely.

Requirements:

- dry-run diff;
- human approval for the exact dry-run mutation plan;
- audit log;
- rollback plan where possible;
- no destructive action by default.

### Phase 4: Agent Harness

Goal: compose read-only tools and planning workflows into a stateful assistant.

Agent responsibilities:

- choose the right workflow for a user goal;
- remember user preferences;
- track todo and review state;
- ask for approval before writes;
- schedule daily and weekly maintenance tasks.

## Core Architecture

```text
Vault
  -> Scanner
  -> Index
  -> Analyzers
  -> Suggestion Planner
  -> Permission Gate
  -> Dry-run Diff
  -> Human Approval
  -> Apply
  -> Audit Log
```

## Core Policies

### Read Policy

Read operations are allowed by default when the user supplies a vault path.

### Write Policy

Write operations require:

1. a SPEC-backed feature;
2. a dry-run plan;
3. human approval for the exact target path and operation;
4. an audit log entry.

### Destructive Policy

Delete operations are denied by default. Archive or move proposals must be
explicitly reviewed.

### Privacy Policy

The core project must work locally. Public fixtures must be synthetic or
sanitized.

## Acceptance Philosophy

A feature is not complete because it "looks right." It is complete only when:

- the SPEC defines the behavior;
- the Worker implements only the approved scope;
- the Reviewer checks the implementation against the SPEC;
- tests or documented manual checks pass;
- changed files and check results are listed;
- risks and non-goals are still respected.
