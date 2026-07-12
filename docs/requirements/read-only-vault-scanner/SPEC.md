# SPEC: Read-Only Vault Scanner

Status: Implemented and independently reviewed. This document remains the
frozen feature contract and historical acceptance baseline.

## Problem

linkloom cannot search, connect, diagnose, or extract work from notes until it
can first produce a trustworthy inventory of a vault. The first slice must
prove that linkloom can read representative Obsidian Markdown without changing
the input.

## User Outcome

The user can run one command against a synthetic sample vault and receive a
stable inventory of its notes, metadata, headings, tags, and WikiLinks.

## Learning Goal

The user should be able to explain two concepts:

1. recursive file scanning: finding every Markdown file below an approved root;
2. structured index: turning note content into a predictable JSON data contract.

The user does not need to learn parser internals or complex Markdown grammar in
this slice.

## Portfolio Evidence

This slice must leave:

- a copyable CLI command;
- automated scanner tests;
- deterministic-output evidence;
- a before-and-after content-hash test proving the input vault is unchanged.

## Scope

The scanner must:

- accept one input directory and one output directory;
- discover `.md` files recursively;
- use only `tests/fixtures/sample_vault/` during development and acceptance;
- produce one note record per Markdown file;
- sort records and list fields deterministically;
- write generated artifacts only outside the input vault;
- continue past a malformed or unreadable note by recording a warning;
- refuse a missing input directory or an output path inside the input vault;
- avoid following symbolic links outside the approved input root.

The scanner reads existing tags only. It must not infer missing tags, normalize
synonyms, create a new tag, or write a tag back into a note. Those are later
governance and human-confirmed mutation concerns.

## Note Data Contract

Each note record in `vault_index.json` contains:

- `relative_path`: POSIX-style path relative to the scanned root;
- `title`: frontmatter title, otherwise first H1, otherwise filename stem;
- `headings`: ordered items with `level`, `text`, and one-based `line`;
- `tags`: sorted unique frontmatter and inline tags;
- `wikilinks`: sorted unique targets found in `[[target]]` or
  `[[target|alias]]` syntax;
- `size_bytes`: source file size;
- `content_sha256`: SHA-256 fingerprint of the original file bytes.

The top-level index contains:

- `schema_version` set to `1`;
- `note_count`;
- `notes`, sorted by `relative_path`;
- `warnings`, sorted by path and warning code.

Absolute private paths, file modification times, and generated timestamps must
not appear in deterministic index content.

### Field Parsing Rules

The Worker must use these exact rules instead of inventing broader Markdown
support:

- Include regular files with a case-insensitive `.md` extension, sorted by
  relative path. Do not follow symbolic links; skip them with
  `SYMLINK_SKIPPED`.
- Recognize frontmatter only when the first line is exactly `---` and a closing
  `---` occurs before body content. Read only `title` and `tags`.
- Accept frontmatter `tags` as one string or a list of strings. Ignore other
  frontmatter fields.
- Title precedence is: non-empty frontmatter title, then first H1 outside a
  fenced code block, then filename stem.
- Store ATX headings only: one to six `#` characters followed by whitespace,
  outside fenced code blocks. Line numbers are one-based source lines.
- Read existing inline tags such as `#agent-evaluation` and `#学习/评测` outside
  fenced code blocks. Deduplicate and sort them with frontmatter tags, but do
  not change spelling or merge aliases.
- Read `[[note]]`, `[[note|alias]]`, `[[note#heading]]`, and
  `[[note#heading|alias]]`; store only the trimmed note target after removing
  alias and anchor. Do not resolve it to a file in this slice.
- `size_bytes` and `content_sha256` always derive from the original raw bytes.
  The hash is 64 lowercase hexadecimal SHA-256 characters.

### Warning And Exit Contract

Each warning must contain `relative_path`, `code`, `message`, and optional
one-based `line`, then be sorted by path, code, and line.

| Warning code | Required behavior |
|---|---|
| `SYMLINK_SKIPPED` | Skip a symbolic-link file or directory; continue. |
| `FILE_READ_ERROR` | Skip unreadable source bytes; continue. |
| `UTF8_DECODE_ERROR` | Skip invalid UTF-8 source; continue. |
| `FRONTMATTER_PARSE_ERROR` | Continue body parsing and title fallback. |

Command-level input and output safety errors are not warnings: exit `2` for a
missing/non-directory input or an output path inside the input root; exit `1`
for an unexpected runtime or artifact-write failure; exit `0` for a completed
scan with zero or more per-file warnings.

## User Workflow

Planned command:

```powershell
python -m linkloom scan tests/fixtures/sample_vault --output .artifacts/scan
```

Planned visible results:

- terminal summary with note and warning counts;
- `.artifacts/scan/vault_index.json`;
- `.artifacts/scan/scan_summary.md`.

## Inputs

- Input: `tests/fixtures/sample_vault/` only for this slice.
- Output: `.artifacts/scan/`, outside the sample vault.
- Encoding: UTF-8 Markdown fixtures.

No private or real Obsidian vault is an allowed implementation, test, or review
input.

The CLI may retain a generic path argument for future reuse, but every command
run, test, and review in this feature must use the synthetic fixture only.

## Outputs

`vault_index.json` is the machine-readable source for later features.
`scan_summary.md` is a short human-readable inventory with counts and warnings.
Neither output may be written into the scanned vault.

## Safety And Permissions

- Scanner modules expose no write, rename, move, merge, or delete capability.
- The only writes are new files below the explicitly supplied output directory.
- The output directory must resolve outside the input root.
- Tests must snapshot every fixture file's bytes before scanning and compare
  them after scanning.
- The Worker and Reviewer must not run this slice against `D:\programblog` or
  any other real vault.

## Failure Modes

- Missing or non-directory input: fail with a clear non-zero exit.
- Output resolves inside input: reject before scanning.
- Invalid UTF-8 or unreadable file: add a warning and continue.
- Malformed frontmatter: add a warning and continue with filename/H1 fallback.
- Symbolic link: skip it and add a warning.
- Output write failure: fail clearly without altering source notes.

## Acceptance Criteria

1. The planned CLI command succeeds on the synthetic sample vault.
2. The fixture covers a Chinese filename, nested folder, frontmatter title and
   tags, inline tag, headings, WikiLink with alias, and malformed frontmatter.
3. `vault_index.json` follows the declared data contract and contains no
   absolute paths or timestamps.
4. Two scans of unchanged fixture content produce byte-identical
   `vault_index.json` files.
5. Tests prove every input file is byte-identical before and after scanning.
6. An output path inside the fixture vault is rejected before any output write.
7. A malformed fixture produces a warning without stopping valid notes from
   being indexed.
8. Relevant unit tests pass without accessing a real vault or the network.
9. Tests use temporary output directories and paths derived from the test
   location, never a shared `outputs/` directory or hard-coded checkout path.
10. Tests prove tags are read from fixtures only; no tag suggestion or tag write
   behavior exists.

## Checks

```powershell
python -m pytest tests/unit/test_scanner.py -q
python -m linkloom scan tests/fixtures/sample_vault --output .artifacts/scan
```

## Non-Goals

- No search, RAG, embeddings, or language-model calls.
- No relation recommendation, duplicate detection, or task extraction.
- No FastAPI, MCP, Agent loop, database, Docker, or desktop UI.
- No generic support claim for every Markdown extension.
- No reading or mutation of a real Obsidian vault.
- No automatic Git commit.

## Implementation Plan

See [implementation_plan.md](implementation_plan.md).

## Tasks

See [task.md](task.md).

## Historical Approval Record

Human approval was recorded before implementation. The Scanner was then
implemented within this contract and independently reviewed. Any behavior
change now requires a new approved feature boundary rather than reopening this
historical contract.

## Handoff Rules

A Worker, including Hajime, must read this SPEC, `implementation_plan.md`,
`task.md`, and `AGENTS.md` before editing. The Worker must stop and ask instead
of expanding scope if a requirement needs a new dependency beyond PyYAML and
pytest, a real vault path, network access, a model key, or a file outside the
approved boundary.

The Scanner must not use the unrelated `relation_eval/` experiment, its model
configuration, prompts, or outputs as a dependency.

## Learning Reflection

- Role: Planner.
- The smallest useful scanner needs a stable source identity and explicit
  immutability proof, not an Agent framework.
- Evidence: this SPEC defines one fixture-only command, deterministic artifacts,
  failure behavior, and objective tests.
- Outcome: implementation and independent review completed; the next product
  feature requires its own planning gate.
