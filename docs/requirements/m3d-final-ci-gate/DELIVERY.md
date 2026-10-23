# M3-D final offline CI gate integration

## Approved boundary

The user's M3 Final CI Gate Integration task is the governing scope: transplant
the existing M3-R CI increment onto fixed M3-D, then gate the existing S/S1/S2/D
contracts. This is regression protection, not new product behavior.

- Repository: `yefuyou/linkloom`.
- Master observed before integration: `46b819fa39ace26de2c5470f7438b56df523c560`.
- Fixed M3-D base: `33390e2eeaad71d215e279b2f724cae672ea4f80`.
- New branch: `codex/m3d-final-ci-gate`, isolated in a new worktree.
- Stacked PR base: `codex/m3s1-s2-combined-acceptance` (PR #13).
- No production, dependency, Provider default, Gold, M2 score or frozen-ledger changes.
- No real vault, Provider Key or paid model request was used.

## Transplant and overlap audit

The source branch adds exactly one commit above M3-R
`417595a2ffc4e02566b7a0dd6e96f143f472e5ca`:
`bcdefda2931b28de9e696d9133bf783643e2dbc9`.
It was reviewed and applied with `git cherry-pick --no-commit` before the final
integration commit. Git auto-merged the Host test; there were no textual
conflicts and no whole-file ours/theirs resolutions.

The Host overlap was reviewed by hunk, preserving both sides:

| M3-R CI behavior retained | S/S2 behavior retained |
| --- | --- |
| Source-bound G fixture, real preparation/registration/Runtime/Reviewer | Explicit discovery output-budget rejection before transport |
| Durable three-tool ledger, request/result SHA and receipt checks | Cold-read rejection of internally consistent role/field tampering |
| Evidence/quote source binding, proposal/P4 separation | v3 discovery wire profile, date pattern and description |
| Hot/cold freshness and revocation checks, empty-candidate rejection | Formal date `2029-06-24` retained in the original import case |

The original M3-R delivery file is retained unchanged as historical provenance;
its old results are not evidence for this branch. The partial-source permission
test and the unregistered-semantic-tool negative control were also transplanted.

## Existing CI entry points

All entries remain in `.github/workflows/offline-pr.yml`. The original Frozen,
Legacy, Core (two repetitions) and Task Investigation checks, dependency setup,
timeouts and event triggers are retained. No new workflow or job was introduced.
The existing `task-investigation` job now has these explicit receipts:

| Named step / JUnit | Test selectors | Local result |
| --- | --- | --- |
| M3-R Product semantic tool chain / `m3r-semantic-chain.xml` | `test_m3q_provider_host.py`, `test_task_investigation_semantic_projection.py`, `test_task_result_projection.py`, `test_unified_ask.py`, unknown-tool ledger negative control | 46 passed |
| M3-S and S2 field binding and task recall / `m3s-field-recall.xml` | `test_task_field_binding.py`, `test_task_recall_protocol.py` | 45 passed |
| M3-S1 bounded replanning through Runtime and Reviewer / `m3s1-replanning.xml` | `test_m3s1_planning_context.py`, `test_m3s1_ambiguous_product.py` | 10 passed |
| M3-D combined F v3 and G replanning contracts / `m3d-combined.xml` | `test_m3d_combined.py` | 6 passed |
| Observation, planning, proof, context, Runtime and HTTP contracts / `task-investigation.xml` | Original list, including TaskObservation, source projection and semantic permissions | Retained; remote receipt required |
| Explicit offline product entry | `scripts/run_m3p_offline_acceptance.py` | Retained; remote receipt required |

The Host test was moved out of the original large invocation to avoid duplicate
execution. All five JUnit files and the original product-entry output are
uploaded by the existing `task-investigation-results` artifact step.

## Focused hardening and local evidence

- The original S1 saved-request test now pins the **24,000-byte** semantic input
  limit, in addition to checking the compact request fits that original limit.
- A negative replan test attempts different work under an already-retained
  UNKNOWN discovery ID. It must be rejected without modifying Host history.
- S1 and D import the transplanted Host loopback-only fixture. Their model
  boundary remains scripted; accidental non-loopback connections fail.
- The D A/B/C assertions and `tests/m3d_support.py` are unchanged. C checks F v3,
  two G plans, retained UNKNOWN discovery, disabled further discover, blocked
  positive dependency, completed independent inspection and accepted Reviewer.
- Local environment: Windows / Python 3.13.5 / google-genai 2.22.0, explicit
  `PYTHONPATH`, disabled pytest plugin autoload, BM25 and empty Provider Keys.
- Initial sandbox HTTP attempts failed with WinError 10013 before Product
  execution; authorized loopback-capable runs passed all 107 focused cases.
- Fresh short directories under project `work/` avoid Windows path pressure;
  no Runtime changes, timeout increases, removed assertions, skip or xfail.
- JUnit evidence is local under `work/ci/`; `git diff --check` passed.
- The transplanted semantic-permission module also passed 15 cases, bringing
  local focused coverage to 122 passing cases with no failures/errors/skips.

## Review and remote evidence

Independent read-only review completed before commit/push: **PASS, P1=0, P2=0**.
The Reviewer inspected the diff, R/S2 overlap, all five local JUnit reports and
the added delivery document without modifying the worktree. The PR description
and final task receipt record the exact remote SHA, workflow_dispatch run, all
jobs, JUnit and downloaded logs. A prior M3-D run is never counted as this branch's CI result. If dispatch
or evidence access is unavailable, the status must be REMOTE_CI_NOT_VERIFIED.

This gate verifies scripted composition and safety contracts; it does not
measure real model recall or semantic judgment.

## Integration after PR #13

Keep this increment as one normal commit above fixed M3-D. After PR #13 merges,
retarget the stacked PR to master if its ancestry remains intact. If #13 was
squashed or rebased, create a branch from updated master and cherry-pick only
the final CI increment, then open a replacement PR and rerun the gate. Do not
cherry-pick the entire M3-D history, force-push, or merge either PR automatically.
