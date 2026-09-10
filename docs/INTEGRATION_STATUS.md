# Integration and push record — 2026-09-10

## Current PR and acceptance state — 2026-09-10

This section is current; the integration snapshot below is retained as dated
history.

- PR #1 is open and mergeable: base `master` at `e5652b4`; head
  `feature/p8-agent-runtime` at `7954eaf`.
- M0.1, M0.2, M0.3, and M0.4 are `ACCEPTED`; M0 is `COMPLETE`.
- Gate A is `COMPLETE`: one bounded real Gemini `gemini-3.8-flash` production
  smoke used `RuntimeEngine` and `GeminiProviderAdapter` for two provider
  turns, one local `search_notes` ToolRuntime call, and a final completed
  AgentResult. The response origin was `provider`, usage was available, and
  the credential-leak check passed.
- Gate B is `COMPLETE`. No further real-provider smoke is required for this
  acceptance; PR merge remains a separate review decision.
- The Team Decision Eval Seed is `ACCEPTED AS EVAL SEED`: 30 cases, six
  workspaces, and 36 notes. Its canonical Git/LF SHA-256 is
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`.
  Golden 8 is **READY TO FREEZE, NOT YET FORMALLY FROZEN**.
- The next product boundary is M1 Team Decision & Action, subject to its own
  approved implementation scope; no M1 production business flow is included
  in this PR.

### Gemini claim boundary

The accepted run used production `RuntimeEngine`, production
`GeminiProviderAdapter`, real Gemini, and local LinkLoom ToolRuntime. The
concrete `google-genai` bootstrap and provider-native second-turn history
reconstruction used for live validation live only in the bounded Gate A smoke
transport. Runtime remains provider-neutral: this does not claim a standalone
production Gemini credential/bootstrap factory or a real-Gemini
process-restart/resume test.

## Historical integration and push snapshot

## Authorization and destination

The user authorized dependency-ordered commits, pushes, and supporting
documentation on 2026-09-10. Earlier task-specific no-commit/no-push statements
remain historical execution boundaries; this authorization permits publishing
the existing work for review. It does not authorize merging master, running a
real provider, or accepting pending data reviews.

Starting remote refs verified through GitHub:

- master: e5652b4cc1c8df0a57f987c7b6504a7708af9976
- feature/p8-agent-runtime: 3000056d7abe7740ecf58fa85ed02a9ab7b65724
- No existing PR was returned by either GraphQL or REST.

Destination: `codex/integration-20260910`, based on the existing P8 commit.
The original feature branch and master remain unchanged. Commits are stacked
in dependency order; later batches include their ancestors.

### Historical commit batches

| Batch | Scope | Acceptance boundary |
|---|---|---|
| 1 | Master V2 and planning navigation | Accepted planning baseline; no production change |
| 2 | M0.1–M0.4 offline Runtime, corresponding tests and records | M0.4 task records human acceptance of M0 offline; Gate A remains partial |
| 3 | Trajectory evaluator, 41 cases, fixtures and tests | Existing feature-level independent acceptance; full-suite limitations retained |
| 4 | Team Decision seed, 30 cases and 36 notes | Corrected data awaiting focused independent re-review; not accepted as eval seed |
| 5 | Optional SDK dependency and manual smoke harness | Offline preparation only; real-provider smoke NOT_RUN |

Batch 2 intentionally combines the accumulated Runtime edits. M0.1–M0.4
overlap in graph, adapter, retrieval and durability code, and no intermediate
commits captured their accepted snapshots. Splitting by filename or guessing
historical hunks would create unverified intermediate states. The component
SPECs and regression tests retain the individual behavioral boundaries.

The M0.4 task record contains both offline Runtime acceptance and later smoke
preparation history. Publishing that record in batch 2 does not mean batch 5
files are present until batch 5 lands.

### Historical validation and merge policy

Validate exported committed trees with their own `src` on PYTHONPATH so local
untracked modules cannot make an incomplete batch pass. Run relevant offline
Runtime, evaluator, seed and smoke checks; exclude the opt-in real-provider
test explicitly. Record results below as they become available.

No current product-test CI run was found on GitHub. The only existing Actions
record was an older Dependency Graph run. Local focused checks do not imply
full-suite success or CI acceptance. Historical full-suite failures involve
legacy external paths and Windows temporary-directory access.

Merge remains a separate decision after reviewing the final commit range and
its evidence. Seed re-review and Gate A real-provider evidence remain visible
open items; neither is silently converted to acceptance by a push.

### Historical exclusions

Do not stage `.tmp/`, `src/test.md`, `tasks/`, generated egg-info changes,
credentials, host environment files, or real-vault artifacts. These pre-existing
local files are preserved. Validation exports are disposable local artifacts.

### Historical verification on the integration branch — 2026-09-10

Published commits:

- `cdc6157` — planning and integration boundaries;
- `0187b21` — offline M0 Runtime closeout;
- `9b62318` — Trajectory Eval;
- `770be3b` — Team Decision seed;
- `5f6430f` — gated real-provider smoke harness and final verification.

The branch is currently pushed through `5f6430f`. Local verification recorded:

| Check | Result |
|---|---|
| M0.4 offline regression | 21 passed |
| M0.3 retrieval regression | 11 passed |
| Retrieval ToolRuntime regression | 10 passed |
| M0.2/P8 durability regression | 106 passed |
| handoff/multi-agent/memory compatibility | 12 passed |
| Trajectory Eval core | 120 passed, 4 unavailable at pytest `tmp_path` setup because of Windows ACL |
| Trajectory Eval CLI | 23 PASS, 0 FAIL, 18 NOT_IMPLEMENTED |
| Team Decision seed validator | 30 cases, 6 workspaces, 36 notes; PASS |
| smoke harness offline | 15 passed, 1 skipped; real Provider NOT_RUN |
| compileall | exit 0; one inaccessible protected pytest directory warning |
| git diff --check | exit 0; existing LF/CRLF notices only |

The four Trajectory Eval errors are environment setup failures before the test
body, not assertion failures. No production test was altered to hide them.
The full repository suite remains a separate known red gate because legacy
relation-eval tests use an unavailable external path and Windows protected
temporary directories. No PR or merge has been performed; the next review
should inspect the pushed commit range before deciding whether to merge any
batch into `master`.
