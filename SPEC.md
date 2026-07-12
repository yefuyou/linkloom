# linkloom SPEC

## Status

Active product contract. Milestone 1's Read-Only Vault Scanner is implemented
and independently reviewed on synthetic fixtures. The next feature remains
planning-only until separately approved.

`SPEC` is the canonical term for project requirements, scope, acceptance
criteria, and implementation gates.

## Mission

Build a local-first personal knowledge assistant that helps users understand,
retrieve, connect, organize, and act on long-lived Markdown notes, then safely
apply only the changes they explicitly approve.

The project must be useful beyond one private vault and credible as an
open-source portfolio project. It prioritizes adapting to an existing vault,
but also supports people gradually building a knowledge base from ongoing
materials, learning, projects, and reflection. Personal vaults may be later
dogfood and case studies, but never hard-coded product assumptions.

## User Outcomes

linkloom must eventually let a user:

1. search and answer questions from notes with inspectable sources;
2. discover possible relationships between notes with evidence and reasons;
3. diagnose duplicate, isolated, weakly tagged, and structurally confusing notes;
4. extract tasks, unresolved questions, and next actions with provenance;
5. preview, confirm, audit, and roll back any approved vault change.
6. support a visible lifecycle from material intake through understanding,
   action, feedback, and reviewed knowledge return.

`Provenance`:
The source trail showing which file and passage produced a result.

## Canonical Roadmap

The product roadmap is [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md):

```text
Milestone 1: Can Read
Milestone 2: Can Find
Milestone 3: Can Connect
Milestone 4: Can Organize
Milestone 5: Can Act
Milestone 6: Modify Only After Confirmation
```

The roadmap owns user value, visible results, minimum capabilities, planned
files, acceptance, risks, and non-goals for every milestone. This SPEC does not
duplicate those details.

## Product Principles

### Local First

Core scanning, indexing, reporting, and rule-based retrieval must work on the
user's machine. Cloud-backed model features must be optional, isolated behind a
provider boundary, and disclose when selected note content may leave the
device.

`Provider boundary`:
A replaceable connection layer between linkloom and a local or cloud language
model.

### Read And Write Separation

Read-only commands must not load or expose write-capable tools. A result from a
read milestone may become an input to a future change plan, but it may not
directly mutate a note.

### Evidence Before Claims

Answers, relation candidates, health findings, and action items must identify
their source files and passages. A score alone is not evidence.

### Human-Controlled Mutation

Any real-vault write requires all of the following:

1. a reviewed feature SPEC;
2. a visible dry-run and exact per-file plan;
3. explicit approval naming the plan and vault path;
4. source-version validation immediately before the write;
5. a recoverable backup created before the first write;
6. audit evidence;
7. a tested rollback path.

`Dry-run`:
A preview that shows what would change without changing any file.

`Rollback`:
A tested way to restore the exact state from before an approved change.

### User Value Before Architecture

Agent loops, embeddings, vector databases, MCP, background jobs, and desktop UI
are implementation options rather than product milestones. They may be added
only when an accepted user workflow requires them.

`MCP`:
A standard protocol that lets AI hosts call external tools and data sources.

### Lifecycle And Progressive Enablement

linkloom must help users make progress through a knowledge lifecycle rather
than force them into a directory taxonomy:

```text
material input -> understanding and sedimentation -> connections
  -> goals, tasks, or experiments -> feedback and reflection
  -> reviewed return to the knowledge base
```

The system first observes existing structure, marks uncertainty, asks only the
questions needed for the user's current pain point, and recommends one useful
read-only capability. It must not reorganize a vault merely because a template
would prefer a different structure.

### Controlled Self-Growth

Self-growth is a later capability with three evidence-backed forms:

1. gradually improved connections among themes, questions, insights, and notes;
2. action continuity from knowledge through outcomes and reflection;
3. editable personal context such as confirmed rules, recurring themes, and
   capability development.

Every derived result must have source evidence and remain visible, editable,
deletable, and reversible. This is not a license for autonomous directory
creation, note rewriting, or opaque long-term memory.

## System Boundaries

```text
Vault input
  -> read-only scan and note index
  -> search / relations / diagnostics / actions
  -> evidence-backed reports and proposals
  -> separate mutation plan
  -> preview and exact human confirmation
  -> apply with audit and rollback
```

The first five milestones remain read-only. Milestone 6 introduces write
capability through a separate permission boundary.

## Non-Goals

- Do not silently rewrite, delete, merge, rename, move, or tag user notes.
- Do not mutate a real vault before Milestone 6 acceptance.
- Do not use a real vault as an early development fixture.
- Do not hard-code `D:\programblog` or any personal folder layout.
- Do not make chat the entire product.
- Do not add multi-agent runtime complexity for presentation value alone.
- Do not require a live Obsidian plugin for the core workflow.
- Do not promise autonomous background maintenance in the initial release.

## Approval Gate

The Scanner implementation is complete. The next feature may not start until
its own SPEC, implementation plan, acceptance checks, and human approval exist.
