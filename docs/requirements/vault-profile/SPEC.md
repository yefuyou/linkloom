# SPEC: Vault Profile

Status: Planning draft. No implementation is authorized until this SPEC and its
implementation plan are independently reviewed and explicitly approved.

## Problem

The completed Read-Only Vault Scanner produces a trustworthy machine-readable
index, but a first-time user still sees raw note records rather than an
understandable portrait of their knowledge base. Before asking the user to
search, connect, organize, or act on notes, linkloom should explain what it can
observe and what it cannot yet determine.

## User Outcome

The user can run one command against a Scanner-produced synthetic index and see
a deterministic, read-only profile containing:

- note and folder distribution;
- observed tag and WikiLink coverage;
- heading and warning counts;
- a clear list of uncertainty and non-claims.

The profile helps a user decide whether Can Find, Can Connect, Can Organize, or
Can Act is the next useful capability. It does not judge whether their current
structure is correct.

## Learning Goal

The user should understand only:

1. derived artifact: a new report calculated from an existing index without
   rereading or changing source notes;
2. deterministic aggregation: the same unchanged index produces the same
   profile, so a changed result has an inspectable cause.

## Portfolio Evidence

This feature must leave:

- a copyable CLI command;
- a documented input/output data contract;
- automated determinism and input-immutability tests;
- a sample profile that visibly separates observations from uncertainty.

## Scope

The profile must:

- accept one `vault_index.json` with Scanner `schema_version: 1`;
- use only a Scanner index produced from `tests/fixtures/sample_vault/` during
  development, testing, and acceptance;
- calculate counts from the index only, without opening original Markdown notes;
- write a machine-readable `vault_profile.json` and readable `vault_profile.md`
  below an explicit artifact output directory;
- sort every collection deterministically;
- preserve the input index bytes exactly;
- make limitations and unknowns explicit.

The profile is an observation layer. It must not infer a topic, classify a note
role, recommend a tag, detect a duplicate, propose a relation, judge note
quality, or recommend a modification.

## Input Contract

The input is the completed Scanner's `vault_index.json`.

Required top-level fields:

- `schema_version` equal to integer `1`;
- `note_count` equal to the number of note records;
- `notes`, each with Scanner fields `relative_path`, `headings`, `tags`, and
  `wikilinks`;
- `warnings` as a list of Scanner warning records.

The Profile must reject malformed JSON, a mismatched schema version, or an index
whose declared note count does not match its records. These are command errors,
not partial observations.

## Output Contract

`vault_profile.json` contains:

- `schema_version` set to `1`;
- `source_index_sha256`, calculated from the original input index bytes;
- `note_count` and `warning_count`;
- `folder_distribution`, grouping notes by first relative-path segment, with
  root-level notes grouped under `_root`;
- `tag_coverage`, including notes with at least one existing tag, distinct tag
  count, and tag frequencies;
- `wikilink_coverage`, including notes with at least one existing WikiLink,
  total extracted links, and target frequencies;
- `heading_coverage`, including notes with headings and total headings;
- `warning_counts`, grouped by Scanner warning code;
- `unknowns`, a fixed sorted list of limitations.

`vault_profile.md` presents the same observations as a short review report. It
must label the limitations section as "What this profile cannot determine."

The profile contains no absolute path, generated timestamp, source note body,
model output, or private error detail.

## Fixed Unknowns

The first profile must explicitly state that it cannot determine:

- whether folders express intentional organization;
- whether a missing tag is a problem;
- whether two notes are semantically related or duplicated;
- why a link is present or absent;
- which user pain point or capability should be enabled next.

These questions require later evidence, user input, or both.

## User Workflow

Planned command:

```powershell
python -m linkloom profile .artifacts/scan/vault_index.json --output .artifacts/profile
```

The command creates only profile artifacts. It does not rescan a vault and does
not take a vault directory as input.

## Safety And Permissions

- The Profile reads the provided index file only.
- It must not open the indexed Markdown paths.
- It writes new artifacts only below the explicit output directory.
- Tests must snapshot input index bytes before and after the command.
- No network, model provider, secret, real vault, or write-capable tool is
  permitted.

## Failure Modes

| Scenario | Required behavior |
|---|---|
| Missing input index | Exit `2` with a clear error; write no artifacts. |
| Malformed JSON | Exit `2` with a clear error; write no artifacts. |
| Unsupported schema version | Exit `2` with a clear error; write no artifacts. |
| Inconsistent note count | Exit `2` with a clear error; write no artifacts. |
| Artifact write failure | Exit `1`; leave the input index unchanged. |

## Acceptance Criteria

1. The planned command succeeds against a Scanner-generated synthetic index.
2. The profile correctly reports the fixture's note, warning, folder, tag,
   WikiLink, and heading aggregates.
3. Two profile runs on unchanged index bytes produce byte-identical JSON and
   Markdown artifacts.
4. Tests prove the input `vault_index.json` remains byte-identical.
5. Invalid JSON, unsupported schema, and inconsistent note count fail before
   artifact creation.
6. Artifacts contain no absolute vault path or generated timestamp.
7. The readable report includes the fixed limitations instead of presenting
   structure counts as semantic judgments.
8. No test or command accesses a real vault, calls a model, or changes a note.

## Non-Goals

- No Scanner changes or Markdown parsing.
- No direct vault-directory input.
- No semantic model, embedding, RAG, Agent loop, database, FastAPI, MCP, or UI.
- No relation, duplicate, topic, tag, task, goal, or role inference.
- No suggestion, preview, confirmation, or mutation of a note.

## Implementation Plan

See [implementation_plan.md](implementation_plan.md).

## Tasks

See [task.md](task.md).
