# linkloom SPEC

## Status

Active product contract: Team Decision Recovery / Temporal Decision
Intelligence. The Scanner was an earlier accepted infrastructure milestone;
it is not the current product endpoint. Semantic Ingestion Steps 1–3 are
implemented in the current source tree. The bounded V1 browser journey was
validated with real Gemini on 2026-10-08; release evidence and limits are in
[the V1.1 backlog](docs/V1_1_BACKLOG.md).

`SPEC` is the canonical term for project requirements, scope, acceptance
criteria, and implementation gates.

## Mission

Help project teams recover the currently valid decision, its historical
changes, the supporting source evidence, temporal validity, and why the
conclusion is believed to be valid. The current product direction builds from
the existing Temporal Decision Memory and retrieval stack toward a governed
raw-artifact-to-authoritative-memory path.

Earlier milestones established deterministic scanning, indexing, and
provenance foundations. The current product direction builds on those
foundations toward Team Decision Recovery. Markdown and Obsidian remain
possible source formats, not the product boundary.

## User Outcomes

LinkLoom should let a team member:

1. ingest timestamped team artifacts into exact, version-bound evidence spans;
2. review source-grounded decision and fact candidates before they become
   authoritative memory;
3. recover current and historical decisions using valid-time semantics;
4. inspect explicit supersession links and the provenance for each record;
5. distinguish source changes and extraction corrections from business
   decisions.

`Provenance`:
The source trail showing which file and passage produced a result.

## Current Architecture Direction

The current product roadmap is [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md):

```text
Raw team artifacts
  -> Semantic Ingestion
  -> candidate decision/fact layer
  -> policy / review / materialization
  -> Temporal Decision Memory
  -> schema-constrained query decomposition
  -> structured bounded retrieval
  -> ContextAssembler
  -> Reader / structured result
```

The Semantic Ingestion boundary is `raw source -> evidence -> candidate ->
validation -> policy/review -> materialization -> Temporal Decision Memory`.
An extraction result alone is never authoritative. The feature SPEC owns the
Step 3 authorization, persistence, and lifecycle contract; this root SPEC
records the product direction and architecture boundary.

## Historical Product Roadmap

The original personal Markdown/Obsidian assistant milestones remain preserved
in `docs/PRODUCT_ROADMAP.md` as historical planning. The completed Read-Only
Vault Scanner remains an accepted deterministic scanning foundation. It does
not define the current product endpoint or authorize real-vault writes.

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
- Do not hard-code machine-specific absolute paths or any personal folder layout.
- Do not make chat the entire product.
- Do not add multi-agent runtime complexity for presentation value alone.
- Do not require a live Obsidian plugin for the core workflow.
- Do not promise autonomous background maintenance in the initial release.

## Approval Gate

The Scanner implementation is complete. The next feature may not start until
its own SPEC, implementation plan, acceptance checks, and human approval exist.
