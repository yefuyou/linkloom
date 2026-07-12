# linkloom Product Roadmap

Status: Active product roadmap. Milestone 1's Scanner is implemented and
independently reviewed on synthetic fixtures; the next feature remains
planning-only until separately approved.

This is the single user-facing roadmap for linkloom. `SPEC.md` defines the
product contract, while `DEV_SPEC.md` maps this roadmap to implementation
work. The detailed synthetic relation-evaluation implementation spine lives in
`docs/requirements/linkloom-master/`; it uses work-package labels rather than
redefining these user-facing milestones. If product milestone descriptions
drift elsewhere, this file wins.

## Product Goal

linkloom is a local-first personal knowledge assistant intended to become a
real, open-source portfolio project. It helps people turn materials,
understanding, actions, and reflection into a trustworthy long-lived loop. It
supports existing Markdown/Obsidian vaults first, while also serving people who
are gradually building a new knowledge base through ongoing input and review.

It helps users search notes with sources, discover hidden relationships,
diagnose knowledge-base disorder, recover unfinished work, and safely apply
approved improvements.

`Local-first`:
The user's notes stay on their own computer by default. A cloud model may only
receive note content through a separately configured and clearly disclosed
provider path.

## Product Promise

- Reading and writing are separate capabilities.
- The default operating mode is read-only.
- Early development uses only a synthetic sample vault.
- A real vault is not a test fixture.
- Every answer, relation, diagnosis, or action item must point back to evidence.
- Real-vault writes require preview, exact confirmation, audit evidence, and a
  tested rollback path.
- Agent mechanisms are introduced only when they improve a user workflow.
- Existing vault structure is observed before organization is recommended; no
  fixed directory taxonomy is required.

`Synthetic sample vault`:
A small fake Obsidian library created for repeatable tests, with no private user
content.

## Milestone Map

```text
1. Can Read
      -> 2. Can Find
      -> 3. Can Connect
      -> 4. Can Organize
      -> 5. Can Act
      -> 6. Modify Only After Confirmation
```

Each milestone builds on the evidence produced by the previous one. Later
milestones may not bypass earlier safety or quality gates.

## Knowledge Lifecycle Alignment

The six technical milestones serve a human knowledge lifecycle rather than a
mandatory folder tree:

```text
materials enter
  -> understanding and sedimentation
  -> connections among themes, questions, and notes
  -> goals, tasks, or experiments
  -> feedback and reflection
  -> reviewed return to the knowledge base
```

| Technical capability | Lifecycle contribution |
|---|---|
| Can Read | Make incoming and existing material visible as a trustworthy inventory. |
| Can Find | Recover evidence needed to understand and reuse knowledge. |
| Can Connect | Reveal meaningful links among materials, questions, insights, and projects. |
| Can Organize | Surface reviewable structural friction without imposing one taxonomy. |
| Can Act | Turn evidence into reviewable actions, experiments, and continuity. |
| Modify After Confirmation | Return only approved, auditable improvements to the knowledge base. |

"Self-growth" is a later result of this loop: evidence-backed knowledge
connections, action feedback, and editable personal context improve over time.
It never permits silent restructuring or opaque long-term memory.

## Milestone 1: Can Read

Status: Scanner implementation complete. Vault Profile remains optional backlog;
the current synthetic relation-evaluation planning spine does not make it a
blocking closure slice.

### 1. User Problem

The assistant cannot help with a vault until it can reliably understand which
notes exist and how each note is structured. The completed Scanner now proves
this safely on a synthetic vault; its output remains the prerequisite for every
later capability.

### 2. Visible Result

The user runs one command against the sample vault and receives:

- a terminal summary with note, tag, link, and warning counts;
- a machine-readable index containing each note's path and structure;
- a human-readable scan summary;
- proof that the input vault did not change.

`Machine-readable index`:
A structured file the program can reliably load again, rather than a report
that only a person can interpret.

### 3. Minimum Capabilities

- Discover Markdown files inside an approved root.
- Parse title, headings, frontmatter, tags, WikiLinks, file size, modified time,
  and content hash.
- Record readable errors without stopping the whole scan.
- Produce deterministic output in a separate artifact directory.
- Refuse paths that escape the approved vault root.

`Frontmatter`:
The metadata block at the top of a Markdown file, often containing tags, dates,
aliases, or status.

`WikiLink`:
Obsidian's `[[Note Name]]` link format for connecting notes.

`Deterministic output`:
Scanning the same unchanged vault twice produces exactly the same meaningful
result.

`Content hash`:
A short fingerprint calculated from file contents so the program can detect
whether those contents changed.

`Artifact directory`:
A separate output folder for generated indexes and reports, kept away from the
source notes.

### 4. Implemented Project Files

- `pyproject.toml`
- `src/linkloom/__init__.py`
- `src/linkloom/__main__.py`
- `src/linkloom/cli.py`
- `src/linkloom/scanner.py`
- `tests/fixtures/sample_vault/`
- `tests/unit/test_scanner.py`
- `docs/requirements/read-only-vault-scanner/SPEC.md`
- `docs/requirements/read-only-vault-scanner/implementation_plan.md`
- `docs/requirements/read-only-vault-scanner/task.md`

### 5. Acceptance

- The documented scan command succeeds on the sample vault.
- Chinese paths, frontmatter, tags, headings, and WikiLinks have test coverage.
- Repeating the scan produces equivalent index content.
- A before-and-after hash check proves the sample vault is unchanged.
- Malformed notes produce visible warnings instead of a process crash.
- No real Obsidian vault is used for Milestone 1 acceptance.

### 6. Main Risks

- Markdown and frontmatter variants may be parsed inconsistently.
- Symbolic links may escape the approved root.
- File timestamps can make otherwise stable output appear different.
- Encoding errors may hide notes or corrupt metadata.

`Symbolic link`:
A filesystem shortcut that can point outside the folder being scanned.

### 7. Explicit Non-Goals

- No question answering.
- No semantic search.
- No relation recommendation.
- No health diagnosis beyond scan/parsing warnings.
- No real-vault write, rename, move, merge, or rewrite.

### Optional Backlog: Vault Profile

The Scanner index is useful to software but still too raw for a first-time user.
Vault Profile may use an existing `vault_index.json` to summarize note and
folder distribution, observed tag and WikiLink coverage, scan warnings, and
uncertain areas. It is a read-only explanation layer, not a semantic classifier.

It is not the current implementation start and must not block the synthetic
relation-evaluation spine defined by `docs/requirements/linkloom-master/`.

Inputs: an existing Scanner index from a synthetic fixture.

Outputs: a deterministic, human-readable knowledge-base portrait with its
evidence and uncertainty clearly separated.

Acceptance: it never reads an unapproved vault, never changes the Scanner's
parsing contract, never calls a model, and never modifies a source note.

Non-goals: no duplicate judgement, relation recommendation, tag suggestion,
topic inference, or automatic restructuring.

## Milestone 2: Can Find

### 1. User Problem

The user remembers a concept or question but not the file, folder, or exact
wording where the answer was recorded.

### 2. Visible Result

The user asks a question and sees:

- the most relevant note passages;
- a concise answer grounded in those passages;
- source file and section references;
- an explicit uncertainty message when the vault has insufficient evidence.

### 3. Minimum Capabilities

- Split notes into stable, addressable passages.
- Support keyword retrieval before adding meaning-based retrieval.
- Rank and return evidence with source locations.
- Generate an answer from retrieved evidence through a replaceable model
  adapter.
- Validate that every emitted citation belongs to the retrieved evidence set.
- Evaluate retrieval and answer behavior against fixed questions.

`Retrieval`:
Finding the small set of note passages most likely to answer a question.

`RAG`:
The program finds evidence first, then asks a language model to answer from that
evidence instead of relying only on model memory.

`Model adapter`:
A small boundary that lets linkloom change model providers without rewriting
the search workflow.

### 4. Planned Project Files

- `src/linkloom/chunking.py`
- `src/linkloom/retrieval/keyword.py`
- `src/linkloom/retrieval/semantic.py`
- `src/linkloom/retrieval/hybrid.py`
- `src/linkloom/answering.py`
- `src/linkloom/citations.py`
- `src/linkloom/providers/`
- `tests/eval/search_questions.yaml`
- `tests/unit/test_retrieval.py`
- `tests/integration/test_cited_answer.py`
- `docs/requirements/cited-note-search/`

### 5. Acceptance

- Fixed questions retrieve the expected supporting passage within the accepted
  top results.
- Every factual answer sentence has a valid source or is marked uncertain.
- A no-evidence question does not receive an invented answer.
- Citation validation rejects a fabricated source reference.
- Local keyword search remains usable without a model key.
- Any cloud-backed answer mode clearly states that selected note content may
  leave the device.

### 6. Main Risks

- Relevant text may be retrieved even though it does not support the conclusion.
- A citation may exist but be attached to a semantically incorrect claim.
- Chinese and English retrieval quality may differ.
- Sending evidence to a cloud model may create privacy risk.

`Semantic retrieval`:
Finding passages that express a similar meaning even when they use different
words.

### 7. Explicit Non-Goals

- No web search.
- No autonomous multi-agent research team.
- No production MCP server.
- No automatic edits based on answers.
- No promise that the first retrieval version handles every language equally.

## Milestone 3: Can Connect

### 1. User Problem

Related ideas live in different folders and years, but the user does not know
which notes should be connected. Obsidian's graph remains fragmented because
explicit links are missing.

### 2. Visible Result

The user receives a ranked related-note report. Every candidate shows both
notes, the passages that caused the match, whether a link already exists, and a
plain-language reason for the recommendation.

### 3. Minimum Capabilities

- Build an explicit graph from existing WikiLinks and tags.
- Generate candidate pairs from content similarity.
- Remove self-links, existing links, and obvious low-information matches.
- Rank candidates with a transparent score breakdown.
- Limit recommendations per note and allow user feedback.

`Knowledge graph`:
A map whose points are notes and whose lines represent known relationships.

`Candidate pair`:
Two notes the system proposes for human review as a possible connection.

### 4. Planned Project Files

- `src/linkloom/graph.py`
- `src/linkloom/relations/candidates.py`
- `src/linkloom/relations/ranking.py`
- `src/linkloom/relations/explanations.py`
- `src/linkloom/reports/related_notes.py`
- `tests/eval/relation_pairs.yaml`
- `tests/unit/test_relation_ranking.py`
- `docs/requirements/explainable-note-relations/`

### 5. Acceptance

- Known related pairs in the sample vault rank within the agreed threshold.
- Every suggestion names both paths and includes evidence from both notes.
- Existing links and duplicate suggestions are filtered.
- Common words alone do not dominate the highest-ranked results.
- Running the feature does not add WikiLinks or modify notes.

### 6. Main Risks

- Shared vocabulary can produce plausible but useless relations.
- Similarity scores can look authoritative without being meaningful.
- Too many suggestions can create more work than value.
- A relation explanation generated by a model may overstate the evidence.

### 7. Explicit Non-Goals

- No automatic WikiLink insertion.
- No automatic taxonomy creation.
- No full graph visualization application in the first version.
- No claim that similarity proves a factual or causal relationship.

## Milestone 4: Can Organize

### 1. User Problem

The user cannot efficiently locate duplicate, isolated, empty, badly tagged, or
structurally confusing notes across a large vault, and generic cleanup rules can
mistake intentional notes for errors.

### 2. Visible Result

The user receives a vault health report grouped by issue type and severity. Each
finding includes evidence, a reason, confidence, and a suggested next review
action. The report changes no source note.

### 3. Minimum Capabilities

- Detect empty and suspiciously short notes.
- Detect duplicate titles and content-similarity candidates.
- Detect broken links and isolated notes.
- Detect missing metadata only against user-configurable rules.
- Detect heading-level and section-structure anomalies.
- Record false-positive feedback for future tuning.

`Diagnostic rule`:
A clearly stated condition used to flag something for review, not a verdict that
the note is wrong.

`False positive`:
A note flagged as problematic even though the user intentionally wrote it that
way.

### 4. Planned Project Files

- `src/linkloom/auditors/`
- `src/linkloom/policies/health_rules.py`
- `src/linkloom/reports/vault_health.py`
- `config/health-rules.example.yaml`
- `tests/fixtures/health_vault/`
- `tests/eval/health_findings.yaml`
- `tests/unit/test_auditors.py`
- `docs/requirements/vault-health-diagnostics/`

### 5. Acceptance

- Seeded issues in the sample vault are found and classified correctly.
- Every finding includes the rule, evidence path, and review recommendation.
- The report distinguishes exact duplicates from possible semantic duplicates.
- User-configurable exclusions prevent known intentional short or isolated notes
  from repeatedly appearing.
- False positives are measured on the sample evaluation set.

### 6. Main Risks

- Personal organization preferences may be mistaken for universal rules.
- Similar content may be complementary rather than duplicated.
- Missing tags may be intentional.
- A single health score can hide important differences between issue types.

### 7. Explicit Non-Goals

- No automatic merge.
- No automatic folder reclassification.
- No automatic rewrite of user prose.
- No universal claim about the one correct knowledge taxonomy.

## Milestone 5: Can Act

### 1. User Problem

Tasks, unresolved questions, and next steps are buried inside daily notes,
project notes, and study records. The user loses continuity between one workday
and the next, and cannot easily connect what they know to what they should test,
do, or review next.

### 2. Visible Result

The user sees one reviewable action inbox containing the original text, source
location, extracted action, status, confidence, and suggested next step. The
user can ask for today's or a project's pending work without changing the
source notes.

Over time, the user may also review proposals that connect a goal or experiment
to its supporting knowledge, missing information, outcomes, and reflection. A
proposal is never silently converted into a user commitment.

### 3. Minimum Capabilities

- Extract explicit Markdown checkboxes.
- Identify unresolved questions and clearly stated next actions.
- Preserve source location and original wording.
- Deduplicate repeated mentions while retaining all sources.
- Filter by project, topic, date, and status when evidence exists.
- Route a request to the appropriate read-only search, relation, health, or
  action workflow.
- Preserve the evidence and unresolved information that an action depends on.

`Action inbox`:
A generated review list that gathers unfinished work without moving or editing
the original notes.

`Agent routing`:
The assistant chooses which approved read-only capability to use for the user's
request, rather than running every tool.

### Long-Term Boundary

Can Act is not limited to extracting existing checkboxes. Later approved slices
may propose goal decomposition, knowledge-dependent tasks, small experiments,
and feedback-to-reflection links. Each result must distinguish source-derived
facts from AI suggestions, retain provenance, and remain reviewable before any
write-back.

### 4. Planned Project Files

- `src/linkloom/actions/extractor.py`
- `src/linkloom/actions/deduplication.py`
- `src/linkloom/actions/query.py`
- `src/linkloom/reports/action_inbox.py`
- `src/linkloom/orchestration/router.py`
- `tests/eval/action_items.yaml`
- `tests/integration/test_action_inbox.py`
- `docs/requirements/action-continuity/`

### 5. Acceptance

- Seeded tasks, unresolved questions, and next actions are found in the sample
  vault.
- Every extracted item links to its source and preserves the original text.
- A normal descriptive sentence is not silently converted into a high-confidence
  task.
- Duplicate mentions are grouped without losing provenance.
- The generated action inbox does not modify source checkboxes or notes.

`Provenance`:
The trace showing exactly which file and passage an extracted item came from.

### 6. Main Risks

- Ordinary prose may be mistaken for a commitment.
- Old completed work may be revived as a new task.
- Suggested next actions may be confused with user-authored obligations.
- Agent routing can hide which tool produced a result unless traces are visible.

### 7. Explicit Non-Goals

- No background scheduler in the first Milestone 5 release.
- No operating-system notifications.
- No automatic task completion.
- No external business-tool actions.
- No autonomous long-running agent that changes the vault.

## Milestone 6: Modify Only After Confirmation

### 1. User Problem

The user wants help applying useful organization changes but cannot trust an AI
that may silently rename, move, merge, or rewrite years of notes.

### 2. Visible Result

The user first sees a numbered, per-file change plan and line-by-line preview.
Only the exact confirmed plan can run. After application, the user sees an audit
record and can restore the previous state.

### 3. Minimum Capabilities

- Convert accepted suggestions into an immutable change plan.
- Display a dry-run and per-file diff.
- Require exact plan ID, vault path, and operation confirmation.
- Verify source content versions before applying.
- Create a recoverable backup before the first write.
- Apply all operations transactionally where possible and stop safely on error.
- Record audit evidence and support tested rollback.

`Dry-run`:
A preview of intended changes that does not modify any file.

`Diff`:
A line-by-line view of what will be added, removed, or moved.

`Content version check`:
A final check that a note has not changed since the preview was created.

`Transaction`:
A guarded operation that either completes as planned or reports and recovers
from partial failure instead of pretending everything succeeded.

### 4. Planned Project Files

- `src/linkloom/mutations/plan.py`
- `src/linkloom/mutations/preview.py`
- `src/linkloom/mutations/permission.py`
- `src/linkloom/mutations/apply.py`
- `src/linkloom/mutations/rollback.py`
- `src/linkloom/audit.py`
- `tests/fixtures/mutation_vault/`
- `tests/integration/test_permission_gate.py`
- `tests/e2e/test_apply_and_rollback.py`
- `docs/requirements/permissioned-vault-mutation/`

### 5. Acceptance

- A write attempt without exact confirmation is rejected.
- Applied changes exactly match the approved preview.
- A stale plan is rejected when any target file changed after preview.
- An injected mid-operation failure produces a clear partial-failure record and
  does not silently continue.
- The mutation fixture can be restored byte-for-byte by the tested rollback.
- A real vault remains forbidden until the complete Milestone 6 gate passes on
  synthetic fixtures and the user separately approves the exact real path and
  plan.

### 6. Main Risks

- A user or another program may edit files between preview and apply.
- Renames may break links or attachments.
- Partial writes may leave the vault inconsistent.
- Backups may be incomplete or stored in an unsafe location.
- Broad confirmation language may be mistaken for exact approval.

### 7. Explicit Non-Goals

- No automatic delete.
- No unattended bulk rewrite.
- No AI self-approval.
- No hidden write tools in read-only commands.
- No real-vault mutation before preview, confirmation, audit, and rollback are
  independently accepted.

## Where The Read-Only Vault Scanner Fits

The Read-Only Vault Scanner is the foundation of Milestone 1, not the product
itself and not an Agent demo. It creates the common note index used by every
later capability:

```text
Read-Only Vault Scanner
  -> note index
     -> cited search and answers
     -> relation candidates
     -> health diagnostics
     -> action extraction
     -> safe mutation plans
```

The scanner establishes one trusted answer to basic questions such as: Which
notes exist? What metadata and links do they contain? Which source path and
content version produced a later result?

## Scanner Acceptance Evidence

The implemented Scanner command is:

```powershell
python -m linkloom scan tests/fixtures/sample_vault --output .artifacts/scan
```

It has been accepted on the synthetic fixture only. It does not authorize real
vault access or any later feature.

The user will be able to run:

- the sample-vault scan command;
- the scanner unit tests;
- a repeatability check that scans the same fixture twice;
- an immutability check that compares source hashes before and after scanning.

The user will be able to see:

- terminal counts and warnings;
- `.artifacts/scan/vault_index.json` with structured note records;
- `.artifacts/scan/scan_summary.md` with a readable inventory;
- test evidence for Chinese paths, metadata, tags, headings, and WikiLinks.

The user will be able to verify:

- linkloom can read the synthetic vault without changing it;
- every indexed record can be traced to a source file;
- repeated scans are stable;
- malformed input is reported instead of crashing the scan.

The user will not yet be able to ask questions, discover semantic links,
diagnose duplicates, extract an action inbox, or modify a note. Those outcomes
belong to Milestones 2 through 6.

## Portfolio Story

The resume value comes from one coherent trust progression, not from listing
unconnected AI technologies:

1. Safe local Markdown ingestion with reproducible fixtures.
2. Citation-backed retrieval and answer evaluation.
3. Explainable knowledge-graph recommendations.
4. Configurable knowledge-base diagnostics with measured false positives.
5. Source-preserving action extraction and read-only Agent routing.
6. Human-confirmed mutation with preview, audit, stale-plan protection, and
   rollback.

This progression may use embeddings, a language model, or an Agent loop where
they earn their place. It does not require a complex multi-agent runtime, MCP
server, desktop UI, or vector database before the corresponding user problem
demands one.

`Embedding`:
A numeric representation used to compare the meaning of text passages.

`Agent loop`:
A controlled cycle where the model chooses an approved tool, receives its
result, and decides whether another step is needed.

`MCP server`:
A standard tool gateway that lets compatible AI applications call linkloom
capabilities. It is optional until external integration has real user value.

`Vector database`:
A storage engine designed to search numeric meaning representations. linkloom
does not need one until simpler local indexes stop meeting measured needs.

`CLI`:
A command-line interface: the text command a user runs in a terminal before a
graphical application exists.

## Approval Gate

Milestone 1 Scanner work is complete. Vault Profile, Can Find, and every later
feature require their own feature-level SPEC, implementation plan, acceptance
checks, and human approval before business code is written.
