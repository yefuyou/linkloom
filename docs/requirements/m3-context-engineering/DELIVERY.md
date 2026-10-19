# M3-C Context Engineering V1 delivery

Baseline: `26392432dcef70692461e6f2f50bc1bd5528f69a`.
Branch: `codex/linkloom-m3-context-engineering`.
Independent Worktree: `D:/webproject/Linkloom/work/linkloom-m3-context-engineering`.
Scope: the authorized M3-C SPEC in this directory. No real vault or live model
was used; no shared Runtime/tool/semantic proof module or frozen evaluation was
edited. Independent review and final verification status are recorded below.

## Delivered capability and call chains

- `ContextAssembler.assemble` -> bounded candidate validation ->
  `select_evidence` -> atomic groups and backfill -> memory/experience gates ->
  `ContextBundle.selection` and `.budget_report`. Defaults retain caps 5/3/2 and
  local 12,000 characters / 3,000 tokens; improvement does not depend on larger caps.
  Only the first configured candidate-prefix is inspected/sorted; omitted tails
  have a bounded count/range record and explicit declared-subgoal missing IDs.
- `ContextPolicy` supplies model identity, window/output/byte limits and an
  injectable tokenizer. `RequestUsage` reserves the caller's other request
  segments. UTF-8 bytes, characters and tokens are all checked on the rendered text.
- `WorkingContext.to_json/from_json` -> injected `Compactor.compact` -> guarded
  `CompactionProposal` -> protected-state preservation -> fresh source recovery ->
  bounded result. FakeModel tests cover successive turns, retained failures and
  conflicts, missing sources and rejected invented references.
- `EvidenceRecovery.restore` -> workspace-scoped authoritative loader ->
  permission/currentness/version/content hash -> exact character/line range and
  quote hash -> immutable reference identity -> original quote or explicit
  `incomplete` with per-reference reasons. Historical mode is explicit and keeps
  the historical label. Missing required IDs also make recovery incomplete.
  Defaults bound source text to 4 MiB, each restored quote to 64 KiB and total
  restored quotes to 256 KiB; oversize recovery stays explicitly incomplete.
  Present permission/currentness flags require exact booleans, and all declared
  group/goal/member counts and hint-ID sizes are bounded before selection.

Detailed baseline loss/recovery distinctions are in [AUDIT.md](AUDIT.md).

## Independent synthetic comparison

The experiment is `benchmarks/context_engineering/`; its new Gold is isolated in
that directory and read only after all strategy outputs have been produced.
Selection receives task subgoals and observable topic/link-group tags, never Gold
labels or required-answer sets. A label-renaming test guards that separation.
Declared groups can also contain stale or redundant requirements; the policy is
a bounded greedy heuristic, not an oracle or optimal selector.

A = frozen top-5-before-fit, 12,000 chars / 3,000 len/4 tokens.
B = same algorithm with caps doubled to 10 / 24,000 / 6,000.
C = improved selection at the original local caps, model window 4,096 minus 512
output tokens and other measured/reserved context, FakeModel compaction and
explicit recovery after a serialized working-state round-trip.

| Metric (8 scenarios) | A | B | C |
|---|---:|---:|---:|
| Required Evidence Coverage | 5.88% (1/17) | 23.53% (4/17) | 88.24% (15/17) |
| Complete subgoal coverage | 0% (0/16) | 18.75% (3/16) | 87.50% (14/16) |
| Counterevidence retained | 0/2 | 0/2 | 2/2 |
| Exact reference tokenizer tokens, total | 52,119 | 67,749 | 3,385 |
| Estimated tokens, total | 12,847 | 16,854 | 2,773 |
| Mean absolute token error per scenario | 4,909 | 6,361.88 | 76.50 |
| Context bytes, total | 144,647 | 184,193 | 9,226 |
| Mean estimated token-budget use | 53.53% | 35.11% | 10.03% |
| Mean exact reference token-budget use | 217.16% | 141.14% | 12.24% |
| Mean serialized compaction size after/before | N/A | N/A | 5.06% |
| Source recovery successes / attempts | N/A | N/A | 2/4 |
| Deterministic context-loss error probes | 3 | 3 | 0 |
| Deterministic context-loss refusal probes | 5 | 5 | 1 |

The exact counts use **tiktoken 0.12.0 `cl100k_base`**, downloaded once as a public
vocabulary and then run locally. These are exact reference-tokenizer counts of
the synthetic rendered text, **not** Gemini/DeepSeek billing or full provider
request tokens. Large Chinese distractors expose the baseline len/4 error. The
new heuristic still underestimates some identifier-heavy text; inject the target
model tokenizer when a hard token bound is required. The UTF-8 byte bound remains
independent of token estimates.

Scenarios cover two/three hops, separated same-file passages, cross-document
conflict, supplier/plan revisions, 180 irrelevant passages, long tool history
with a short context and key-original restoration, plus source updates/deletion.
The final scenario deliberately has no recoverable current source: 2 invalid
restores are blocked (version mismatch and missing source), leaving 2 required
facts uncovered and an explicit incomplete/refusal result. Valid archived
restoration succeeds 2/2. The 50% overall restore rate includes these deliberate
failures and is not advertised as 100%.

The error/refusal probe is a deterministic evaluator of stale, one-sided and
missing evidence. It does not call an LLM, measure answer quality or demonstrate
an automatically compressing Agent. The experiment is a development mechanism
comparison with source tags, not a held-out generalization claim. Full per-case
sizes, omissions and failures are in [experiment.json](experiment.json).

Reproduction (PowerShell, from this Worktree):

```powershell
$env:PYTHONPATH='src'
$env:TIKTOKEN_CACHE_DIR=(Join-Path $PWD 'work/tokenizer-cache')
python -m benchmarks.context_engineering.run --output docs/requirements/m3-context-engineering/experiment.json
```

`tiktoken` is an optional experiment dependency already available on the host;
production dependencies were not changed. A new host needs the package and its
public vocabulary. Contract/experiment unit tests use an injected Fake tokenizer
and require no tokenizer download.

## Verification

- First affected regression: **91 passed, 1 failed**. The sole failure is
  `test_m11_verified_evidence_context.py::test_gemini_request_serializes_verified_context_into_model_visible_input`.
  The exact same 1 failure / 4 pass result was reproduced against the requested
  baseline's source tree. Gemini search projection emits `read_required=true`
  rather than the quote asserted by that old test. Provider code is outside scope.
- Canonical `scripts/run_validation_regression.py`: **4 collection errors**.
  Baseline lacks `scripts/run_memoryagentbench_g3_pilot.py` and
  `scripts/run_stage_c_context_memory_loop.py`; `git cat-file -e BASE:path`
  confirms both are absent. No frozen score or unrelated runner was repaired.
- Initial new contract/experiment tests: **29 passed** (`new-tests.xml`). Review
  then found two validation gaps and an output-size concern; red-first tests
  reproduced **20 failures** before remediation. Final affected verification
  after those fixes is recorded below.
- **Final affected verification: 174 passed, 2 failed**, across 176 tests,
  including **all 51 new tests passed**. The second failure is
  `test_retrieval_v2_agent_integration.py::test_mps001_retrieved_evidence_ids_round_trip_to_same_source_and_version`:
  raw backend candidates are 17, while the test expects 6, before Assembler
  filtering. Both failed test IDs were rerun against the unchanged requested
  baseline source and reproduced identically: **2 failed**. See
  [final-affected-tests.xml](final-affected-tests.xml) and
  [baseline-affected-tests.xml](baseline-affected-tests.xml). No new failure was
  observed in this completed affected test set. This is not a full-suite claim.
- Exploratory full unit/integration runs were attempted for both current and
  unchanged baseline sources with identical offline/current-retrieval settings.
  Both exhibited numerous failures and slow unrelated legacy benchmark tests;
  the current run progressed beyond 53%, the baseline beyond 42%, before both
  were stopped to finish the scoped review fixes and affected regressions.
  **These broad runs are incomplete, their full failure sets are unclassified,
  and no full-suite pass or baseline/current equivalence is claimed.**
- Windows pytest uses the existing `pytest_windows_tempdir` plugin and fresh
  `work/` basetemp paths. Runtime adapter tests use existing `current` retrieval;
  network/model downloads are disabled with HF/Transformers offline variables.
  The original default-Hybrid attempt was stopped during model loading; it is
  not counted as a completed pass. No live-provider smoke suite was run.

The final affected set covers ContextAssembler, all new context contracts,
verified model context, durable/Fake model loops, ledger/runtime tools, retrieval
V2/Agent integration, temporal memory/reconciliation/write policy and manifest/
index isolation. Reproduction uses `PYTHONPATH='src;scripts'`,
`LINKLOOM_RETRIEVAL_MODE='current'`, `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, the existing `pytest_windows_tempdir` plugin and a fresh
`LINKLOOM_PYTEST_BASETEMP` under `work/`. Exact commands and artifact hashes are in
`verification.json`. `git diff --check` passed.

## Remaining Runtime wiring

No automatic Agent compression, recovery-tool registration or product entry
point was added. The next integration must:

1. Supply task-plan subgoals and independently derived evidence groups at
   `RuntimeAgentAdapter._assemble_context`; missing/unknown groups stay reported.
2. At the request-building boundary in `SingleAgentModelLoop`, measure other
   serialized segments and pass `RequestUsage`/the actual model's `ContextPolicy`.
   This round does not change the existing 8-result/24 KiB/96 KiB boundaries.
3. Persist WorkingContext JSON beside the existing checkpoint, with immutable
   ledger/checkpoint digest references. Existing RuntimeState schema is unchanged.
4. Provide an authoritative permission-aware current-source loader and convert
   existing verified passages to exact `EvidenceRef` locators. The new IDs are
   an independent namespace; persist the mapping rather than guessing an ID.
5. Trigger compaction and explicit recovery at a Runtime-owned budget boundary,
   reserve working-hint/quote/envelope costs and recheck the final provider request.
   Only restored original quotes can support business conclusions. Fail closed
   on incomplete sources. Real model Compactor use requires separate budget approval.

The simpler larger-budget alternative was measured. Local architecture remains
four small responsibilities (policy, selection, recovery, working state), using
the existing Assembler and no new execution Runtime or production dependency.

## Acceptance and review

Independent read-only review: **final qualified acceptance of scoped M3-C**.
All findings from both review passes were fixed and regression-tested; the final
targeted review found no additional substantive issue. See [REVIEW.md](REVIEW.md).
The independent-capability acceptance criteria are satisfied with the documented
baseline failures and incomplete broad-regression limitation. CI and deployment
were not run or claimed.
Automatic Agent compression and model-specific Runtime coordination remain
explicit follow-up integration work, not completed acceptance claims.

Reflection: the earliest destructive context boundary is the pre-ledger search
filter; a durable ledger only protects observations that reached it. Source
restoration and operational summaries therefore need separate authority and
interfaces. Next step is Runtime-owned wiring after this branch is reviewed.
