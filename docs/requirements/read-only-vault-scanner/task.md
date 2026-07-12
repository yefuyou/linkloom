# Task Ledger: Read-Only Vault Scanner

Status: Implemented and independently reviewed.

## Current Milestone And Feature

- Milestone: 1 - Can Read.
- Feature: Read-Only Vault Scanner.
- Active slice: fixture-only scanner and deterministic note index.

## Completed Work

- [x] Product scope constrained to one minimum runnable scanner.
- [x] Synthetic-only input and real-vault prohibition documented.
- [x] Note data contract documented.
- [x] Planned file boundary documented.
- [x] Acceptance and read-only proof defined.
- [x] Independent planning review passed.
- [x] Detailed fixture, test-matrix, warning, and handoff requirements added.
- [x] Detailed planning review passed.
- [x] Human approved the SPEC and implementation plan.
- [x] Worker implementation completed within the approved file boundary.
- [x] Independent implementation review passed.

## Current Task

Scanner acceptance evidence is complete. Do not expand Scanner scope until the
user approves the next feature.

## Fixed Decisions

- [x] Scanner extracts existing frontmatter and inline tags only.
- [x] Scanner never recommends, normalizes, merges, creates, or writes tags.
- [x] All Scanner validation uses `tests/fixtures/sample_vault/` only.
- [x] Artifacts must resolve outside the input fixture root.
- [x] Relative paths, deterministic ordering, and source-byte hashes are part
  of the first index contract.
- [x] `relation_eval/` remains a separate future relation-evaluation experiment
  and is not a Scanner dependency.

## Planned Implementation Checklist

### Slice A: Package And Discovery

- [x] Create only approved package and CLI files.
- [x] Create the synthetic fixture tree.
- [x] Add recursive discovery test with nested and Chinese paths.
- [x] Record package import and discovery behavior through Scanner tests.

### Slice B: Existing Structure Extraction

- [x] Implement title precedence.
- [x] Implement heading records with line numbers.
- [x] Extract existing frontmatter and inline tags.
- [x] Extract WikiLink targets without alias/anchor.
- [x] Add size and SHA-256 based on original bytes.
- [x] Add malformed-frontmatter warning behavior.

### Slice C: Safe Output

- [x] Reject missing input and output-inside-input before artifact writes.
- [x] Generate deterministic `vault_index.json`.
- [x] Generate `scan_summary.md` and terminal counts.
- [x] Verify output avoids private absolute paths and timestamps through tests.

### Slice D: Safety Evidence

- [x] Add repeated-scan byte comparison.
- [x] Add fixture before/after byte snapshot comparison.
- [x] Add temporary-output isolation.
- [~] Add optional symlink behavior; test is skipped when Windows blocks link creation.
- [x] Run planned Scanner commands and record factual results.
- [x] Submit Worker evidence for independent review.

## Test Record

- Scanner test command: `D:\Anaconda\python.exe -m pytest tests/unit/test_scanner.py -q`.
- Result: `8 passed, 1 skipped`.
- The skipped test is symbolic-link behavior when Windows does not grant link
  creation permission.
- No real vault has been read or modified.

| Check group | Status | Evidence or blocker |
|---|---|---|
| SCN-01 to SCN-08 parsing | Passed | Covered by `test_scanner.py`, including redacted read errors. |
| SCN-09 to SCN-10 path safety | Passed | Missing input and output-inside-input rejection tested. |
| SCN-11 deterministic output | Passed | Two generated JSON files are byte-identical. |
| SCN-12 input immutability | Passed | Before/after fixture byte snapshots match. |
| SCN-13 symbolic link | Skipped | Windows link permission was unavailable. |
| Real vault and network access | Not applicable | Forbidden for this feature. |

## Learning Points

- Planned concept 1: recursive scanning inside an approved root.
- Planned concept 2: deterministic structured index and data contract.

## Interview Evidence

Planned evidence:

- runnable CLI scan command;
- automated parser and safety tests;
- deterministic JSON output;
- before-and-after input-byte comparison;
- malformed-input warning case.

## Required Worker Evidence

Before review, add concise factual entries below. Do not paste large logs,
private paths, credentials, or generated artifact contents.

- Actual changed files: `pyproject.toml`, `src/linkloom/**`,
  `tests/fixtures/sample_vault/**`, `tests/unit/test_scanner.py`, this ledger.
- Installation result: editable install completed with PyYAML and pytest already
  available in `D:\Anaconda`.
- Scanner test result: 8 passed, 1 skipped.
- Hajime fixture addition: `综合结构测试.md` verifies frontmatter title
  precedence, heading order, tag and WikiLink deduplication, code-fence
  exclusion, and source-byte hashing.
- Fixture scan result: 5 notes indexed, 1 warning, JSON and summary generated
  under `.artifacts/scan`.
- Deterministic-output comparison: passed in Scanner test.
- Input-byte snapshot comparison: passed in Scanner test.
- Malformed-frontmatter warning: `FRONTMATTER_PARSE_ERROR` recorded while the
  note body remained indexed.
- Output-inside-input rejection: command returned exit code 2 before writing.
- Read-error privacy: simulated private-path failure produced a stable redacted warning.
- Skipped checks and reasons: symbolic-link test skipped because Windows did not
  allow link creation.
- Real-vault/network/secret/Git-commit confirmation: no real vault, network,
  provider, secret, or Git commit used for Scanner work.

## Remaining Issues

- Initial planning Reviewer verdict: PASS.
- Implementation Reviewer verdict: PASS after read-error privacy fix.
- Symbolic-link behavior needs a later run on a Windows environment with link
  creation permission.

## Next Candidate Task

Complete the product-document calibration, then create a separate planning
boundary for a read-only Vault Profile. Do not begin Vault Profile, search,
relation evaluation, tag governance, or any real-vault work without explicit
approval.
