# linkloom Product Roadmap

Status: Active product roadmap for Team Decision Recovery / Temporal Decision
Intelligence. On 2026-10-08, the V1 Sources → Review → Decision Memory → Ask →
source-update/reconciliation journey was validated through the product browser
UI with real Gemini on one synthetic fixture. V1 feature work is frozen;
follow-on features are in `docs/V1_1_BACKLOG.md`. `SPEC.md` defines the current
product contract.

Feature SPECs, delivery records and acceptance evidence were moved out of this
repository on 2026-11-13; `b2c3821` is the last commit that contains them.

## Product Goal

LinkLoom helps project teams recover the currently valid decision, its history,
supporting evidence, temporal validity, and why the conclusion is believed to
be valid. Meeting notes, timestamped conversations, project documents, and
decision logs are source artifacts; the product outcome is an auditable
temporal decision record and a trustworthy answer.

## Delivered capabilities

| Date | Capability | Validation scope |
|---|---|---|
| 2026-10-14 | Unified Ask: one form routes to reviewed Decision Memory or the bounded temporal Agent, pins workspace/decision/date and discloses the execution source. | Offline HTTP and browser |
| 2026-10-15 | Natural-language decision discovery with candidate selection and exact-date clarification. | Offline HTTP and browser |
| 2026-10-15 | Answer-first presentation: answer, readable UTC dates and original evidence before audit metadata; Chinese/English copy. | Offline HTTP and browser |
| 2026-10-15 | Bounded two/three-hop source-relation investigation through the Runtime, with partial and conflict results. | One independent run: 8/10 |
| 2026-10-15 | Human-governed feedback memory: propose, approve and revoke workspace terminology used by discovery. | Offline HTTP and browser |
| 2026-10-15 | Real-Agent reliability sprint on new synthetic cases with Gemini 3.8 Flash; two source/workspace identity defects fixed. | First frozen run: 12/16 |
| 2026-10-21 | Cross-meeting task investigation in Ask; saved results revalidate evidence and lifecycle without model calls. | Offline synthetic mode |
| 2026-10-21 | Gemini task stages with separate execution permission, per-question source snapshots and shared budgets. | Scripted offline transport; real provider not run |
| 2026-10-21 | STALE decision revalidation in Review Attention with explicit new-candidate approval. | HTTP/SQLite; browser pending |

Known gaps: the M2 task-relation acceptance cases H01–H04 still fail (0/4), and
meeting-task extraction recall is below the whole-meeting baseline on the
held-out sets (see the README).

## Current Product Architecture

```text
Raw Team Artifacts
        -> Semantic Ingestion
        -> Candidate Decision / Fact Layer
        -> Policy / Review / Materialization
        -> Temporal Decision Memory
Runtime Agent
        -> Grounded TeamDecisionResult
        -> Reviewer
        -> AgentMemoryCandidate
        -> Shared review / authorization lifecycle
        -> DecisionMaterializer
        -> Temporal Decision Memory
User / project preferences and terminology
        -> Generic Agent Memory (`src/linkloom/memory/`)
User Query
        -> Schema-constrained query decomposition
        -> Bounded temporal retrieval
        -> ContextAssembler
        -> Reader / Structured Result
```

The authoritative boundary is:

```text
Raw source -> Evidence -> Candidate -> Validation -> Policy / Review
           -> Materialization -> Temporal Decision Memory
```

An extraction result alone never becomes authoritative memory. Agent Memory
capture stores a reviewable candidate; a successful Runtime answer or Reviewer
PASS does not authorize materialization. Semantic Ingestion and Agent Memory
share candidate lifecycle infrastructure while preserving their separate
contracts.

Generic Agent Memory (`src/linkloom/memory/`) remains distinct from Temporal
Decision Memory (`src/linkloom/decision_memory/`): it holds approved user and
project context such as preferences and terminology, while Temporal Decision
Memory represents temporal business decisions. A grounded TeamDecisionResult
must pass the Reviewer before it becomes an AgentMemoryCandidate; the shared
review/authorization lifecycle gates the DecisionMaterializer.

The V1 Sources UI supports explicit artifact updates. It creates a new
immutable version, resolves affected decisions through exact evidence identity,
runs reconciliation, and surfaces STALE records in Review Attention. This is
not an OS-level file watcher: arbitrary external file edits/deletions are not
automatically reconciled.

## Development Evidence (Not Held-out Evaluation)

The following results are preserved as development evidence only:

- V1 Temporal: 2/92.
- V1 Flat BM25 Top-5: 5/92.
- V2 semantic-plan Temporal: 39/92.
- Fixed planner pilot: lexical 4/16; schema-constrained semantic planner
  12/16.
- In the same V2 development setup, Reader context was about 68% smaller than
  Flat.

These results do not establish clean held-out performance or generalization.
The underlying sealed benchmark packets are retained locally and are not part
of the V1 release claims.
