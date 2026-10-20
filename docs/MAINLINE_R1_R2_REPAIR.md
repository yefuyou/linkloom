# PR #3 R1/R2 repair boundary

Baseline: `5a5e0ce71f71e5c89247a1efb028f01fcbf9f60f`.
The user authorized a separate clean worktree, ordinary commits and a push to
the existing PR branch. Keep PR #3 Draft. No Ready, merge, other feature or
production Runtime change is authorized.

## R1: obsolete positive-test expectations

The two original multi-action files reproduce **2 failed / 16 passed** at the
baseline. The failures are outdated test expectations, not malformed input
schemas or evidence that the production Runtime needs weaker safeguards:

- `test_runtime_keeps_only_the_newest_whole_tool_turns_within_history_limit`
  returned empty search results throughout, then expected 24 calls and a Final.
  The existing progress guard instead returns `MODEL_PROGRESS_STALLED`.
  The history-window positive fixture now returns a distinct synthetic evidence
  ID for each query. It retains the last two whole turns, checks all 24 executor
  calls in order, and checks durable result hashes/payloads against the Ledger.
  The original all-empty scenario remains a separate regression requiring the
  safe stop, no later proposal execution and no successful Final.
- `test_policy_denial_is_one_ordered_result_and_later_call_still_runs` expected
  another tool and a Final after a permission denial. The existing recovery
  contract terminates with `MODEL_UNRECOVERABLE_TOOL_ERROR`, reason
  `TOOL_PERMISSION_DENIED`. Its renamed regression requires that termination,
  no denied/later executor, no further model call and no Final. The ordered
  successful/denied result artifacts remain durable and are checked against
  their hashes and the executed-call Ledger.

The existing budget test still forbids executing any over-budget member; it now
also checks that only the allowed call enters the Ledger. Its observation-only
Final follows the existing `STOP_TOOL_USE` recovery contract, without additional
tool execution. No production progress, permission, budget or recovery rule is
changed, and no skip/xfail is added. The full multi-action/resume files and the
proposal/progress/recovery contract modules now run in both offline Core jobs.

Working-tree validation before the delivery commit: **19/19** multi-action and
resume tests; **77/77** related focused regressions. A sandbox temporary-directory
permission error in the first focused attempt was environmental; the controlled
system-temp run passes. Final-SHA local runs and actual Actions receipts are
reported separately at delivery, not inferred from these preliminary results.

## R2: historical authority requirement (previous repair)

Target:
`docs/evaluation/public_memory_benchmark_clean/e2_revised_mh_only_20261005/final_evaluation_v1_20261005/formal_rehearsal_budget_ledger.json`.

Original seal SHA-256:
`a882d54781f8c505558c459a5bc0ab17726723f67dfe06e8545c505a06386640`.

Historical introduction `e411f648c3eca20bf6d0ae3281c476454d47cad2` and the PR
baseline both reference Git blob `e17f5256b2bff8de85fd08470a2d9a9d33e9b4d8`:
3,807 LF bytes, SHA-256
`2d15276cd3da5105e870b8fb7f3640f580fddf216874e617f437be40a006b990`.
The Windows worktree has 3,888 bytes / 81 CRLF and matches the seal because
`core.autocrlf=true`; no path-specific attributes protect the target.

The seal-matching CRLF content would have Git object ID
`21396e54627cf9277b9fe2cf42e87c4a671a96c3`, but that object is absent locally
and the GitHub blob endpoint returns 404. This is a diagnostic candidate,
**not** a recovered authoritative object. A worktree hash match or a calculated
LF-to-CRLF conversion does not prove a historical source.

Independent read-only review scanned all 8,786 reachable and dangling Git blobs
(114,751,121 raw bytes): no blob has the original seal SHA-256. The matching
`git archive` output also changes back to the nonmatching LF content when
`core.autocrlf=false`, so it is environment-derived, not an authoritative archive.

The user's narrower restoration rule requires original bytes from an
authoritative historical Git object. Until such a source is identified, R2 is
**BLOCKED**. Do not stage the worktree's converted bytes, add a misleading
passing integrity gate, or change the seal/ledger/scores. The ledger, its seal,
all other frozen data and production source remain unchanged in this repair.
A successful R1 CI run therefore does not make both blockers resolved.

## R2: newly authorized seal-validated reconstruction — 2026-10-20

The subsequent user instruction explicitly permits deterministic LF-to-CRLF
reconstruction from the already-committed LF blob, only if the full original
seal digest matches and JSON semantics are unchanged. This supersedes the
previous restoration-only restriction above; its historical investigation and
BLOCKED outcome remain recorded. No historical original blob was found.

Source commit: `470cdd8948417c54df4dd124be6265889bcc7f80`.
Source blob: `e17f5256b2bff8de85fd08470a2d9a9d33e9b4d8`, 3,807 bytes,
81 LF, no CR. Replace each LF with CRLF without JSON reserialization. Result:
3,888 bytes, 81 CRLF, SHA-256 exactly
`a882d54781f8c505558c459a5bc0ab17726723f67dfe06e8545c505a06386640`.
Parsing both byte streams yields identical JSON, and removing only the added
CR bytes reproduces the source blob exactly. The reconstructed Git blob is
`21396e54627cf9277b9fe2cf42e87c4a671a96c3`.

This is reconstruction validated by the already-committed seal, **not recovery
of a historical Git object**. The original seal blob remains
`02040f9cc414b018e9e3e7e925ef09903f981c71`; no seal, score, ledger field or
other frozen file is regenerated or edited.

The root `.gitattributes` applies `-text` only to this exact ledger path.
`test_frozen_rehearsal_ledger.py` checks committed HEAD bytes, working-tree
bytes and the original seal independently, plus the attribute and both Git
clean/checkout filter directions under autocrlf false/true/input. The dedicated
offline CI job runs the same checks on native Linux and Windows checkouts.
Existing Core (including both full multi-action files and progress/recovery
contracts) and Legacy Historical jobs remain unchanged.

The new integrity suite reports four failures and one pass against the prior
HEAD's blob/attributes. Its final-commit results and the actual Core, Legacy and cross-platform CI
receipts are recorded in delivery evidence. No production `src/`, Provider
permission or product behavior is changed. Keep this PR Draft; this repair
does not authorize Ready, merge, Release or another PR.
