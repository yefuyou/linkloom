# Implementation Plan: Read-Only Vault Scanner

Status: Implemented and independently reviewed. This is the historical plan
that governed the completed Scanner work; it is not authorization for new code.

Governing SPEC: [SPEC.md](SPEC.md).

## Product Result

One fixture-only CLI command scans Markdown notes and emits a deterministic JSON
index plus a readable summary without modifying input files.

## Learning Result

Focus on only:

1. recursive filesystem scanning with explicit path boundaries;
2. a stable structured-data contract verified by tests.

## Interview Result

Leave a runnable command, automated tests, deterministic output, and an
immutability test that can be demonstrated and explained.

## Approved File Boundary

The Worker may create or modify only:

- `pyproject.toml`;
- `src/linkloom/__init__.py`;
- `src/linkloom/__main__.py`;
- `src/linkloom/cli.py`;
- `src/linkloom/scanner.py`;
- `tests/fixtures/sample_vault/**`;
- `tests/unit/test_scanner.py`;
- `docs/requirements/read-only-vault-scanner/task.md` for factual progress.

Any additional file requires a new human decision before editing.

## Technical Baseline

- Python standard library for CLI, paths, hashing, JSON, and text extraction.
- `PyYAML` only for safe frontmatter parsing.
- `pytest` for acceptance tests.
- No framework, service, database, model API, or Agent runtime.

Permitted dependencies are PyYAML and pytest only. If either is unavailable,
record the environment blocker instead of replacing it with a larger framework.

## Fixture Contract Before Code

Create synthetic fixture notes before implementing parsing. The prose may be
invented, but these facts must be represented and asserted:

| Suggested relative path | Required contents | Expected proof |
|---|---|---|
| `00-扫描入门.md` | Valid frontmatter title/list tags, H1, inline tag, WikiLink. | Frontmatter title wins; tags combine and sort. |
| `projects/设计笔记.md` | No frontmatter title, H1, H2, aliased WikiLink. | Nested discovery, H1 fallback, alias removal. |
| `无标题.md` | No title and no H1. | Filename-stem fallback. |
| `损坏-frontmatter.md` | Invalid opening frontmatter plus valid body structure. | Warning plus continued body parsing. |

Do not commit a symbolic-link fixture. Create one in a temporary test directory
only when the platform permits it; otherwise record a skipped test with the
permission reason.

## Worker Slices

Implement in this order. Each slice is small enough to hand off independently,
and each completion must be recorded factually in `task.md`.

### Slice A: Package And Discovery

- Add the minimal Python package entry point and Scanner module.
- Add the synthetic fixture tree.
- Add one test for recursive, sorted `.md` discovery.

Stop condition: package imports and the discovery test passes. Do not add
metadata parsing or artifact writing before this checkpoint.

### Slice B: Existing Structure Extraction

- Add title precedence, headings with source lines, existing tags, WikiLink
  targets, file size, and SHA-256 fingerprint.
- Add malformed-frontmatter warning handling.
- Assert every field against the fixture contract above.

Stop condition: all accepted fields have a fixture assertion. Do not generate
new tags or resolve WikiLinks to files.

### Slice C: Safe Deterministic Artifacts

- Validate input/output roots before writing.
- Reject output-inside-input.
- Sort notes, tags, WikiLinks, and warnings.
- Produce JSON, readable summary, and concise terminal counts.

Stop condition: two unchanged scans produce byte-identical JSON and all output
is outside the fixture root.

### Slice D: End-To-End Safety Evidence

- Snapshot every fixture file's relative path and original bytes before scan.
- Compare the full snapshot after scan.
- Add missing-input and output-inside-input tests.
- Add optional symlink test and one CLI-facing test.

Stop condition: all evidence is captured, or an environment blocker is stated
without claiming a pass.

## Planned Implementation Steps

### Step 1: Package And Fixture Walking Skeleton

- Add minimal Python packaging and `python -m linkloom` entry point.
- Add a synthetic fixture with representative Markdown cases.
- Add one smoke test proving Markdown files are discovered recursively.

Checkpoint: the CLI starts and the smoke test passes before metadata extraction
is expanded.

### Step 2: Parse The Accepted Note Fields

- Read original bytes once for size and SHA-256.
- Decode UTF-8 and parse title, headings, tags, and WikiLinks.
- Store relative paths only.
- Convert parse failures into sorted warning records.

Checkpoint: unit tests cover every accepted field and malformed frontmatter.

### Step 3: Emit Safe Deterministic Artifacts

- Resolve and validate input and output boundaries before writing.
- Reject output paths inside the input vault.
- Sort notes, tags, WikiLinks, and warnings.
- Write `vault_index.json` and `scan_summary.md` below the output directory.
- Print concise terminal counts.

Checkpoint: repeated scans create byte-identical JSON.

### Step 4: Prove Read-Only Behavior

- Snapshot every fixture file's relative path and bytes before scanning.
- Run the scanner.
- Compare the complete snapshot after scanning.
- Test that output-inside-input is rejected before any write.
- Confirm no test references a private vault path.

Checkpoint: the immutability and path-boundary tests pass.

## Test Plan

Unit and CLI-facing checks must cover:

- recursive discovery and deterministic ordering;
- Chinese and nested paths;
- title fallback order;
- heading levels and line numbers;
- frontmatter and inline tags;
- WikiLink target extraction with alias removal;
- malformed frontmatter warning and continued scan;
- symbolic-link skip behavior where supported;
- missing input failure;
- output-inside-input rejection;
- byte-identical repeat output;
- byte-identical input before and after scanning.

The following matrix is mandatory for review:

| ID | Scenario | Expected result |
|---|---|---|
| SCN-01 | Nested Markdown files | Every note is found in sorted order. |
| SCN-02 | Chinese filename | Relative path is preserved in JSON. |
| SCN-03 | Valid frontmatter title/tags | Title and tags follow the contract. |
| SCN-04 | H1 and filename fallbacks | Title precedence is exact. |
| SCN-05 | Headings | Levels, text, and one-based lines are correct. |
| SCN-06 | Inline plus frontmatter tags | Existing tags are sorted and unique. |
| SCN-07 | Aliased/anchored WikiLink | Stored target excludes alias and anchor. |
| SCN-08 | Malformed frontmatter | Warning exists; body fields remain indexed. |
| SCN-09 | Invalid input root | Clear failure with exit code `2`. |
| SCN-10 | Output inside input root | Rejected before any artifact write. |
| SCN-11 | Repeated scan | Index bytes are identical. |
| SCN-12 | Input immutability | Full fixture byte snapshot is unchanged. |
| SCN-13 | Symbolic link | Skip with warning when platform permits. |

No network, model key, real vault, or external service is allowed.

## Test Isolation Rules

- Use pytest temporary directories for all output artifacts.
- Derive paths from the test location or temporary root; never hard-code
  `D:\webproject` or another checkout path.
- Use the active Python interpreter for subprocess tests rather than a bare
  `python` executable name.
- Do not choose the lexically latest shared output directory.
- Do not reuse `relation_eval/outputs/` or any prior run artifact.

## Planned Commands

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/unit/test_scanner.py -q
python -m linkloom scan tests/fixtures/sample_vault --output .artifacts/scan
```

## Expected Evidence

- passing pytest output;
- terminal scan counts;
- `.artifacts/scan/vault_index.json`;
- `.artifacts/scan/scan_summary.md`;
- test evidence proving fixture bytes are unchanged;
- one recorded malformed-input warning.

Before review, the Worker must also provide: exact changed files, the output
inside-input rejection result, a declaration that no real vault/network/secret
was used, and every skipped check with its reason.

## Risks And Responses

- Markdown ambiguity: support only the syntax named in the SPEC and test it.
- Unsafe paths: resolve paths and reject output inside input before writing.
- Non-determinism: omit timestamps and absolute paths; sort all collections.
- Parser failure: warn per file and continue indexing valid files.
- Scope growth: reject search, API, Agent, database, and real-vault additions.

## Review Checkpoint

Before implementation, an independent Reviewer must verify that this plan:

- matches `SPEC.md`;
- uses only a synthetic fixture;
- has no real-vault command or test;
- separates input reads from artifact writes;
- defines objective immutability and deterministic-output evidence;
- contains no unrelated future architecture.

The human must then approve the reviewed plan. The Worker cannot self-approve.

## Handoff To Hajime

Give Hajime this instruction with the feature folder:

> Implement only Slices A through D in order. Read the feature SPEC, this plan,
> task ledger, and AGENTS.md first. Use only the approved file boundary. Input
> is synthetic fixture data. Existing tags are read but never suggested or
> written. Stop if a new dependency, real vault, network call, model key, or
> out-of-bound file becomes necessary. Update only task.md with factual progress
> and test evidence. Do not commit.
