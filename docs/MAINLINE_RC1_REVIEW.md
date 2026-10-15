# Mainline RC1 review packet

Historical RC1 evidence retained unchanged below. RC2 repairs and gate separation
are recorded in [MAINLINE_RC2_REVIEW.md](MAINLINE_RC2_REVIEW.md); subsequent
results do not erase the failures observed here.

Date: 2026-10-15. Target: `master`. Scope: integrate the accepted product baseline,
add an offline PR gate, and prepare a Draft PR. This packet does not approve a merge.

## Exact integration boundary

- Base: `48c8eb450e1150ad23f4219f6c292e0c5d8658c2` (remote default `master`,
  verified through GitHub; unprotected at inspection).
- Accepted development tip: `49736a7098b23c8b9f021f98f1fe6d3e7c70e755`.
- Candidate: `codex/linkloom-mainline-rc1`, created at that exact tip in a separate
  worktree. Integration-only CI/report and privacy corrections follow it; no Agent
  feature work is included.
- Original range: 35 commits; 539 changed files, +148,711/-1,103 lines.
- GitHub had only PRs #1 and #2, both merged, before this Draft was created.
- Remote heads were enumerated with `git ls-remote --heads origin`; the local
  origin fetch configuration tracks only selected branches, so `branch -r` alone
  was not treated as a complete remote inventory.

Ancestor-contained milestones include V1 release, Temporal Agent integration,
Unified Ask, transport integration at `b766f05`, natural-language discovery,
Product Consolidation (`b2cbe03`), bounded multi-hop (`70052cd`), Feedback Memory
(`5be2e14`), Unified Investigation (`c657e7e`), and reliability (`49736a7`).
Older integration, Golden8, P8 runtime, and local `master` (`c2b4e77`) are also
ancestors. They must not be cherry-picked or merged again.

Three local branches are not ancestors of the accepted tip:

| Branch | Head | Disposition |
| --- | --- | --- |
| Slimming | `4f18c4f` | Patch-integrated, not ancestry-merged. Product Consolidation's delivery records all 20 paths. The 15 non-documentation paths (including retired file absence) exactly match `b2cbe03`; documentation/context changes were preserved separately. No repeat integration. |
| Gemini transport investigation | `eb09f69` | Investigation branch has separate history; approved transport behavior is already present through `b766f05`. No blanket merge of investigation artifacts. |
| Local transport integration | `58ef237` | Local 900-second experiment-budget commit is not included. The remote branch points to the accepted ancestor `b766f05`. |

All other enumerated remote heads are ancestor-contained, except the same
patch-integrated Slimming branch. Local recovery branch was at `49736a7` with
16 status entries; its uncommitted work is excluded. No other worktree was edited.
Read-only worktree inspection also found pre-existing changes in the primary
checkout (203 entries), Gemini investigation (1), V1 RC (2), and temporal
hardening (59). These were not incorporated. Counts are an inspection snapshot,
not a claim about other agents' later work.

## Product and architecture

The core journey is Sources → Review → Decision Memory → Ask. Immutable source
versions and exact evidence spans feed semantic candidates. Explicit local human
review and materialization create temporal decisions, including valid-time,
supersession and source reconciliation/STALE attention. Proposals stay supporting-only.

Unified Ask prefers deterministic reviewed current/as-of lookups. Scope discovery
and reviewed workspace terminology assist routing. Ordinary two/three-hop source
investigations use bounded task plans and verified parent-read dependencies, and
never become approved business decisions simply because Reviewer accepts them.

Major boundaries:

- Agent: `agents/`, `runtime/`, `tools/`, `ui/investigation.py`, `ui/unified_ask.py`.
  Durable multi-action proposals, checkpoints, permission gates, read visibility,
  source identity, Final validation and provider-uncertain terminal handling.
- Memory: `semantic_ingestion/`, `decision_memory/`, `memory/`,
  `ui/feedback_memory.py`. Temporal business truth, generic governed memory,
  feedback routing and Agent candidates remain separate authority domains.
- Retrieval: `context/`, `retrieval_v2/`, scanner/services, BM25/local dense/hybrid,
  directory scope and version-aware incremental indexing/reconciliation.
- No second runtime, evidence store, provider ledger, or unapproved Recovery
  optimization is introduced by RC preparation.

## Reproduced local verification

All product Python checks below ran with `LINKLOOM_RETRIEVAL_MODE=bm25`,
`PYTHONPATH=src;.;tests/integration` on Windows/Python 3.13.5. Model responses were
injected fakes. **Real Gemini/DeepSeek calls in this integration task: zero.**

| Check | Result |
| --- | --- |
| Fresh isolated venv, `pip install -e '.[dev]'` | PASS; core/dev installation succeeds without Provider SDK |
| Wheel build, `pip wheel . --no-deps` | PASS; wheel generated, 620,018 bytes |
| Fresh installed UI launch, isolated synthetic database, no Provider flags | PASS; loopback HTTP 200 and Sources HTML |
| UI launch help / JS syntax / diff whitespace | PASS |
| Ask DOM harness | PASS: stale responses, query/date/scope continuation, workspace ownership, per-request permission reset, feedback-save ordering, escaping and nonaccepted Final suppression |
| Fresh core/product/safety selection (same selection as new workflow) | **155 passed, 5 failed**, 568.12s |
| Host-environment narrower baseline selection | **93 passed, 5 failed**, 526.43s |
| Source identity isolated diagnosis | **12 passed**, 1.88s |
| Historical provider contract selection on host with httpx | **40 passed, 81 failed**, 89.52s; fake clients only |
| Broad historical suite collection at original accepted code | **4 collection errors**, missing untracked experiment scripts |
| Isolated source-investigation diagnosis, stop at first failure | **5 passed, 1 failed**, 129.42s; exposed HOLD-G regression returned `refused` rather than expected `partial` |
| Privacy correction: evaluation isolation / credential isolation | **2 passed** in fresh venv / **4 passed** on host; direct single/batch gate probes also pass with zero requests |

The fresh selection includes source identity/context isolation, temporal memory,
reconciliation, materialization authorization, durable uncertain model loops,
permission gates, grounding visibility, bounded multi-hop, scope discovery,
Feedback Memory and complete product HTTP tests. Passing cases cover current and
historical formal decisions, Sources/Review/materialization, source updates,
workspace isolation, source/version/evidence identity, feedback BEFORE → PENDING
→ APPROVED → REVOKED (including fresh-process replay), and source investigation
permission/UNCERTAIN/no automatic resend.

The initial sandbox run had 40 pass/58 setup errors (loopback sockets and temp
directory access). It is an environment failure, superseded by the explicit
loopback-enabled runs above, not counted as product acceptance.

### Baseline failures retained without weakening assertions

All these results were obtained before any product code modification, directly
at accepted commit `49736a7`. Subsequent RC corrections affect CI, this packet,
three private-path occurrences and a legacy executable default; no tested Agent,
Memory, Retrieval or UI behavior was changed. No frozen Gold, benchmark
expectations or historical scores changed.

1. Three-hop HTTP polling deadline: fresh run timed out while status was still
   INVESTIGATING at 10 model turns/3 search/7 read; host run timed out in another
   three-hop case at 11 turns. Browser three-hop completed and passed Reviewer.
   This establishes environment-sensitive latency, not a passing HTTP test.
2. Legacy `test_temporal_http_provider_unknown_keeps_read_source_without_verified_final`
   parameters 3 and 4 return 500/`TOOL_INVALID_ARGUMENTS` before the intended
   UNCERTAIN request; assertions expect 503. Parameter 1 and separate durable
   model/source-investigation uncertain boundaries pass. The failing coverage
   must be repaired and reviewed separately, not silently excluded from the gate.
3. Two product tests require ignored `work/temporal_decision_eval/GOLD.json` and
   `CASES.json`. Those original files are absent from a clean checkout. No Gold
   was copied, regenerated, changed, or invented.
4. Collection imports absent `scripts/run_memoryagentbench_g3_pilot.py` (three
   test modules) and `scripts/run_stage_c_context_memory_loop.py` (one module).
   These exist as untracked work in the primary checkout, not in the accepted
   Git snapshot. This is a historical reproducibility gap.
5. Old Gemini tests use obsolete model/temperature contracts. Failures include
   `model_id_mismatch` and `temperature_unsupported_for_gemini_3`. Do not relax
   production validation to satisfy them. A fresh dev-only install additionally
   cannot collect `test_p85_gemini_api_adapter.py` because `httpx` is undeclared
   in the dev extra; the host fake-client run above reproduces actual assertions.
6. The additional isolated source-investigation run failed the exposed HOLD-G
   regression (`refused` vs `partial`), after five passing tests including two-hop.
   That case passed in the larger selections. This variability is an unresolved
   reproducibility issue; no rerun was used to erase an earlier result.
7. The unrelated legacy credential test imports `relation_eval`, whose `pydantic`
   dependency is absent from core/dev. Fresh collection fails; the existing host
   environment runs its four assertions successfully. No extra dependency was
   silently added to the core installation.

Local raw diagnostic logs/JUnit files stay under ignored `work/` and are not
published as Provider logs. Canonical filenames: `fresh-focused.{log,xml}`,
`baseline-focused-unsandboxed.{log,xml}`, `baseline-full.{log,xml}`,
`old-provider-global.{log,xml}`. Results are summarized here for a clean checkout.

### Actual browser acceptance (synthetic/fake only)

Launched the existing `run_unified_ask_demo.py --source-investigation` entry,
opened all four screens, and operated the real browser:

- Current supplier: Wrenwell, original Oct 7 quote, explicit zero-model path.
- Oct 6 historical supplier: Birchline, exact quote and valid-time caveat.
- Review: Cedar visibly supporting-only, excluded from Decision Memory.
- Decision Memory: approved current supplier and budget shown.
- Workspace switch: old question and result cleared.
- Three-hop: Owen Brook, all three relationships/citations shown, Reviewer accepted,
  11 turns; ordinary relationship label retained.
- Conflict: Tess Avery retained, Devon Hart and Casey North sources displayed,
  no unique leader, 9 turns.
- Missing downstream evidence: Riley Fox retained, leader unknown, 8 turns.
- **Two-hop Chinese Orion demo failed**: `MODEL_ADAPTER_FAILED`, 7 turns,
  2 search/4 read, no tool errors; UI suppressed an unverified Final. The HTTP
  parameterized two-hop test passed. This browser discrepancy remains a blocker,
  not evidence of two-hop acceptance or model generalization.

## Historical real-model evidence and limits

These are reviewed historical reports, not reruns in this task:

- V1 synthetic browser release recorded six real Gemini GenerateContent requests,
  all accepted first attempt (README and V1 release evidence).
- Reliability sprint reports 50 requests: 47 with usage, 3 UNCERTAIN; successful
  two/three-hop canaries and regressions, one actually exposed inline-injection
  case, partial single-shot RAG controls, and unresolved negative cases.
- Known usage estimate $0.30025725; observed estimate including unknown reserves
  $0.68732925, not a billing receipt. See `requirements/real-agent-reliability/DELIVERY.md`.
- Frozen reliability first score stays 12/16. Two citation-observer field
  mismatches, unsupported-vs-refusal classification, and same-file partial
  evidence are documented rather than rescored here.
- Host compiled narrow plans in the live sprint; this does not validate autonomous
  model planning. Samples are small, synthetic and partly exposed; no universal
  extraction, held-out generalization, provider success-rate or production claim.
- Repeated full-source verification is costly at 500 sources; safe reuse and
  missing/conflict recovery improvements are outside this integration.

## Safety, publication and CI boundary

The new Actions workflow runs installation, launch help, the explicit core/safety
selection and DOM checks on PRs to master. It uses read-only contents permission,
no repository secrets, no model SDK, no paid Provider and no real vault. It keeps
test failures visible and uploads JUnit even on failure. A selected offline gate
does not certify the historical full suite. CI outcome must be read from the
actual PR checks; local verification is not CI verification.

Runtime permission, workspace, source identity, evidence and human materialization
boundaries remain intact. UNCERTAIN is never an automatic resend instruction.
Local actor/confirmation fields are owner assertions, not authenticated RBAC.
This is a loopback local application, not a multi-user production service.
No real vault, Provider request, release or master write was performed here.

Independent review inventoried all 539 original changed tracked paths, examined
the permission/source/uncertainty boundaries and publication candidates, and found
no actual key, bearer token, private key, personal source content or raw Provider
request/response body. Pattern-scan matches for credential shapes were synthetic
test literals. The budget reconciliation JSONL is aggregate hashes/cost events,
not a raw Provider payload. This is bounded audit evidence, not a guarantee from
a full line-by-line review of every historical change.

Three machine-path findings were corrected: the legacy fail-closed CLI now uses
the generic `agy` executable default, and two historical reports explicitly mark
path redaction while preserving results. Evaluation-safety tests (2) and host
credential-isolation tests (4) pass. Single and batch adapter probes confirm the
unchanged gate blocks process execution with zero requests. These paths already
exist in base/master history; no history rewrite was performed or authorized.
Remaining absolute path mentions are historical explanatory prose, not stale
clickable local links. README/run entries align with the current product boundary;
historical test counts are not presented here as current green results.

## Merge readiness and reviewer workflow

**Keep Draft. Not Ready for Review / not recommended for merge while the above
acceptance and reproducibility blockers remain.** Passing safety tests do not
erase baseline failures. Detailed independent publication review is recorded in
the PR description together with its limits.

This is a very large historical integration. If GitHub hides or truncates diffs,
use the complete local Git range, not only the files rendered in the browser:

```bash
git fetch origin master codex/linkloom-mainline-rc1
git log --oneline origin/master..origin/codex/linkloom-mainline-rc1
git diff --stat origin/master...origin/codex/linkloom-mainline-rc1
git diff --numstat origin/master...origin/codex/linkloom-mainline-rc1
git diff --binary origin/master...origin/codex/linkloom-mainline-rc1 > mainline-rc1.patch
git diff origin/master...origin/codex/linkloom-mainline-rc1 -- src/linkloom/runtime src/linkloom/tools src/linkloom/agents
git diff origin/master...origin/codex/linkloom-mainline-rc1 -- src/linkloom/decision_memory src/linkloom/semantic_ingestion src/linkloom/memory
git diff origin/master...origin/codex/linkloom-mainline-rc1 -- src/linkloom/retrieval_v2 src/linkloom/context src/linkloom/ui
```

Review in modules: source/ingestion and human authority; temporal/feedback memory;
retrieval/index identity; Agent/runtime/provider permissions; UI/HTTP state;
tests/fixtures; documentation/evidence/package/workflow. Inspect each historical
commit as needed; PR UI truncation does not reduce review scope.

Before merge:

- [ ] Resolve or explicitly disposition the exact baseline and browser blockers.
- [ ] Make required acceptance reproducible from tracked synthetic inputs without changing historical Gold.
- [ ] Read actual offline CI outcome and re-run only checks affected by corrections.
- [ ] Complete independent architecture/security/publication review and human review.
- [ ] Confirm no Recovery work or private/raw Provider artifacts entered the final diff/history.
- [ ] Recheck base/head and approve the merge explicitly in a separate request.

Prefer **Merge Commit**, preserving the continuous ancestor chain used by ongoing
branches. Do not squash this baseline without considering its effect on dependent
work. No merge is authorized by this packet.

After a separately approved merge, Recovery should commit only its accepted
increment, fetch the updated master, compare `origin/master...<recovery-branch>`,
and open an increment-only PR targeting master. A merge commit retains `49736a7`
as an ancestor, so the historical 35 commits naturally disappear from that diff.
If RC introduces overlapping fixes, resolve them in the Recovery branch after
approval, preserving its work; do not reset, force-push or duplicate old commits.

Reflection: a clean candidate exposes historical untracked-input and contract
drift that a long-lived developer checkout can conceal. The next integration
step is to close those specific evidence gaps, not add Agent capabilities.
