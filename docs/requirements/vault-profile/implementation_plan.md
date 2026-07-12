# Implementation Plan: Vault Profile

Status: Planning draft. No implementation is authorized yet.

Governing SPEC: [SPEC.md](SPEC.md).

## Product Result

One read-only CLI command turns an existing synthetic Scanner index into a
deterministic knowledge-base portrait. The user can inspect aggregate structure
without exposing note bodies to a model or changing any input artifact.

## Learning Result

Focus only on:

1. derived artifacts: reports derived from a stable index rather than from
   direct source-file access;
2. aggregation contracts: counts and grouping rules that are explicit enough to
   test and explain.

## Interview Result

Leave a CLI demo, schema-validation tests, deterministic output evidence, and
an input-index immutability check. The interview story is that Linkloom begins
with transparent observations before it makes recommendations.

## Proposed File Boundary

After approval, the Worker may create or modify only:

- `src/linkloom/cli.py`;
- `src/linkloom/profile.py`;
- `tests/unit/test_vault_profile.py`;
- `docs/requirements/vault-profile/task.md` for factual progress.

No Scanner source file, real-vault fixture, provider configuration, relation
experiment, or other roadmap document may be changed during implementation.

## Technical Baseline

- Python standard library for JSON, SHA-256, paths, grouping, and Markdown
  artifact generation.
- Existing package and CLI entry point.
- `pytest` for tests.

No new dependency is permitted. The feature consumes Scanner schema version 1
as-is; it must not add a database or typed-model refactor just to aggregate
counts.

## Worker Slices

### Slice A: Validate And Aggregate Index Data

- Load raw index bytes once and compute the source-index SHA-256.
- Parse and validate the minimal input contract.
- Aggregate folder, tag, WikiLink, heading, and warning counts.
- Define a fixed, sorted limitation list.

Stop condition: unit tests assert all fields using an index generated in a
temporary directory from the synthetic Scanner fixture.

### Slice B: Deterministic Artifacts And CLI

- Add a `profile` subcommand that accepts an index path and output directory.
- Emit `vault_profile.json` and `vault_profile.md` below that output directory.
- Sort all collections and omit timestamps and absolute paths.

Stop condition: two runs on identical index bytes generate byte-identical
artifacts.

### Slice C: Boundary And Failure Evidence

- Snapshot input index bytes before and after profile generation.
- Test malformed JSON, unsupported schema, and mismatched note counts.
- Verify failures create no partial artifacts.
- Verify the implementation never opens fixture Markdown files after the Scanner
  has generated the temporary input index.

Stop condition: all acceptance checks pass or an environment blocker is
explicitly recorded without claiming success.

## Test Plan

| ID | Scenario | Expected result |
|---|---|---|
| PRF-01 | Valid Scanner index | Aggregates match synthetic fixture. |
| PRF-02 | Root and nested notes | Folder groups use `_root` and first path segment. |
| PRF-03 | Existing tags and links | Coverage and frequency counts are sorted and correct. |
| PRF-04 | Warnings and headings | Counts match source index records. |
| PRF-05 | Repeat run | JSON and Markdown artifacts are byte-identical. |
| PRF-06 | Input immutability | Index bytes are unchanged after profile generation. |
| PRF-07 | Malformed JSON | Exit `2`; no artifacts. |
| PRF-08 | Unsupported schema or inconsistent note count | Exit `2`; no artifacts. |
| PRF-09 | Report limitations | Fixed unknowns are visible in Markdown. |
| PRF-10 | Privacy | No absolute paths, note bodies, model calls, or real vault use. |

## Planned Commands

```powershell
python -m pytest tests/unit/test_scanner.py tests/unit/test_vault_profile.py -q
python -m linkloom scan tests/fixtures/sample_vault --output .artifacts/scan
python -m linkloom profile .artifacts/scan/vault_index.json --output .artifacts/profile
```

## Risks And Responses

- A count can look like a diagnosis: label all results as observations and show
  fixed unknowns.
- Scanner schema drift: reject unsupported schema rather than guessing.
- Hidden source access: construct test indexes in temporary output directories
  and test only the index path passed to Profile.
- Scope growth: reject semantic classification, recommendations, and model use.

## Review Checkpoint

Before implementation, an independent Reviewer must verify that this plan:

- matches `SPEC.md`;
- consumes only a synthetic Scanner index;
- adds no source-note or real-vault access;
- has deterministic and immutability evidence;
- introduces no model, relation, or organization behavior;
- keeps all writes confined to explicit profile artifact output.
