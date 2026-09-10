# SPEC: LinkLoom Portfolio Completion Master V2

Status: **ACCEPTED AND FROZEN — Planning Baseline V2**.

Independent Reviewer verdict: **`PASS_WITH_FINDINGS`**, with no blockers. The
documentation findings were applied on 2026-08-29 under explicit human
instruction. They clarified current evidence, Gate ownership, M0.1 scope, and
the final freeze step; they did not change the seven Gates, the M0-M5 order,
the product direction, or the canonical six-stage product roadmap. No Gate
became complete through this documentation correction.

This freeze authorizes only **M0.1 P8 integration repair** as the next planning
boundary. It does not authorize production-code work: M0.1 still requires its
own Child SPEC, implementation plan, and explicit human approval.

## 1. Authority And Source-Of-Truth Boundary

The canonical product contract remains [the root SPEC](../../../SPEC.md), and
the only canonical user-facing milestone roadmap remains
[docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md).

This document narrows the first job-ready, end-to-end product story and defines
portfolio completion gates. It does **not** create a second product roadmap:

- the canonical product milestones remain `Can Read -> Can Find -> Can Connect
  -> Can Organize -> Can Act -> Modify Only After Confirmation`;
- M0-M5 below are a gated implementation sequence for this portfolio slice,
  not replacement product milestones;
- the existing [linkloom-master/SPEC.md](SPEC.md) is retained as the historical
  synthetic relation-evaluation implementation spine, not as a competing
  active product contract;
- existing P8.x records are evidence inputs. They do not automatically close a
  V2 Gate;
- every implementation increment still requires an approved Child SPEC,
  implementation plan, task record, human boundary approval, Worker evidence,
  and independent Reviewer acceptance.

If this document conflicts with the root SPEC, `AGENTS.md`, or
`docs/PRODUCT_ROADMAP.md`, the repository-level contract wins until a separate
planning change is explicitly approved.

## 2. Problem

Project knowledge is spread across meeting notes, proposals, work logs, and
historical decisions. A conclusion may change over several discussions, while
rejected alternatives and unfinished actions remain buried in older files.
Users need more than generic document chat: they need a trustworthy answer to
questions such as:

- What was finally decided?
- Why was that decision made?
- Which alternatives were rejected?
- What remains unresolved?
- What should happen next?

LinkLoom already contains useful foundations for scanning, retrieval, tools,
model-loop durability, memory, evaluation, tracing, and safe writeback. These
foundations are not yet one accepted production path. The current portfolio
risk is therefore fragmentation: strong mechanisms can be demonstrated
individually, but the repository cannot yet prove one coherent business task
from user request through evidence, model decisions, recovery, evaluation, and
human-approved action.

## 3. Portfolio Product Positioning

For the job-ready V2 slice, LinkLoom is an **evidence-grounded Team Decision &
Action Agent**. It restores project context from meeting records, project
documents, work logs, and historical decisions so a user can understand what
was decided, why it was decided, what was rejected, what remains unfinished,
and what to do next.

Markdown and Obsidian-style workspaces are the current local-first data-source
implementation. They are not the business problem or the entire product
positioning. The first accepted end-to-end dataset must remain synthetic; it
must not imply deployment inside a real bank or other real enterprise.

This positioning narrows the first demonstrable vertical slice without
deleting the broader personal-knowledge outcomes in the root product SPEC.

## 4. North Star

> LinkLoom 是一个面向团队决策与行动恢复的 evidence-grounded Agent：它能够自主检索工作知识、结合受控记忆调用工具、恢复历史决策与未完成事项，并通过人工确认安全执行写回；整个 Agent 过程可追踪、可恢复、可评测。

## 5. Primary User Workflow

The first accepted workflow is `Team Decision & Action Agent`:

```text
User question
  -> model decides whether and how to retrieve
  -> search_notes
  -> read_verified_note
  -> current verified evidence + relevant controlled memory
  -> context assembly
  -> model decides whether evidence is sufficient
       -> insufficient: another allowed tool call
       -> sufficient: grounded final result
  -> Decision
     + Rationale
     + Rejected Alternatives
     + Unresolved Items
     + Next Actions
     + Evidence
  -> optional change proposal
  -> preview
  -> exact human confirmation
  -> permission + backup + apply + audit/rollback on synthetic fixture
```

The minimum demonstrable question is:

> 找出某项目最近讨论的最终决定、被否决方案、未完成事项和下一步行动，并提供证据来源。

The result must distinguish source-derived facts from model suggestions. A
missing or conflicting evidence set must produce an explicit uncertainty or
no-evidence result rather than a plausible invention.

## 6. Scope

V2 Gate scope includes exactly the seven capabilities below:

1. connect one real model provider to the durable production Agent path;
2. make the production path model-driven rather than a fixed Python workflow;
3. assemble retrieved evidence, controlled memory, and tool observations into
   bounded model-visible context;
4. complete the Team Decision & Action business vertical slice;
5. reuse the existing permissioned writeback foundation on synthetic fixtures;
6. evaluate components, trajectories, and business outcomes;
7. quantify failure recovery and at least one honest baseline comparison;

After all seven Gates pass, a separate Portfolio Freeze final step packages a
reproducible demo, architecture story, measured results, and resume/interview
evidence. Portfolio Freeze is not an eighth Gate.

## 7. Evidence Status Vocabulary

Gate status must use one of these meanings:

- **VERIFIED FOUNDATION**: accepted evidence exists for a reusable lower-level
  capability, but the portfolio Gate may still be open.
- **PARTIAL**: implementation or evidence exists, but integration, review, or
  one or more Gate criteria are missing.
- **NOT IMPLEMENTED**: no accepted production behavior exists for the stated
  outcome.
- **BLOCKED**: the next proof requires an explicit external or human gate such
  as credentials, budget, or approval.
- **COMPLETE**: every Gate criterion has objective evidence, independent
  Reviewer acceptance, and human acceptance. Code presence alone never earns
  this status.

Uncommitted or concurrently modified files may inform risk analysis, but they
must not be reported as accepted capability.

## 8. Current Baseline As Of 2026-08-28

No portfolio Gate is currently complete.

| Gate | Current evidence | Missing completion evidence | Status |
|---|---|---|---|
| A — Real Model | Provider-neutral request/response/error/usage contracts and an offline-tested `GeminiProviderAdapter` exist. | P8.5 records say Gemini is not connected to `SingleAgentModelLoop`; no legal real-provider smoke run was executed; P8.5 correction still requires independent re-review. | PARTIAL / BLOCKED for network proof |
| B — Real Agent Loop | `SingleAgentModelLoop`, ToolRuntime, durable artifacts, checkpoints, and FakeModel loop tests exist. | The loop accepts the legacy `ModelAdapter.decide()` contract; production RetrievalAgent remains a deterministic `search -> read` workflow, including current uncommitted ToolRuntime rewiring. The model does not yet control production retrieval or termination. | PARTIAL |
| C — Retrieval + Memory + Context Assembly | The `ModelTurnRequest` contract already supports `previous_tool_call`, alongside user input, tool definitions, and one observation. Memory lifecycle/store/retriever foundations exist. | `SingleAgentModelLoop` does not yet populate or use `previous_tool_call` in the production provider flow; activation belongs to M0.2 durable provider integration. Retrieved evidence and controlled memory are not assembled through one explicit production context contract. `RuntimeAgentAdapter.injected_memory` currently returns memory references after execution rather than exposing selected memory to the model. | PARTIAL |
| D — Business Vertical Slice | Scanner, lexical retrieval, verified-note reading, Ask/Connect services, and synthetic fixtures provide reusable inputs. | No accepted Team Decision & Action dataset, result schema, Agent workflow, or E2E acceptance exists. | NOT IMPLEMENTED |
| E — Human-In-The-Loop | ChangePlan, preview, exact approval, backup, apply, audit, rollback, writeback runtime, and synthetic tests exist. | The capability is not integrated with the Team Decision & Action result. This Master does not establish canonical Milestone 6 acceptance, and real-vault mutation remains forbidden. | PARTIAL |
| F — Agent Eval | Relation/component evaluation exists. Current uncommitted, experimental worktree trajectory evidence records 41 cases across 15 categories: 23 executable and 18 explicitly `NOT_IMPLEMENTED`. | The worktree counts are a useful foundation, not accepted Gate F completion. Business outcome evaluation, decision accuracy, groundedness, unresolved-item recall, unsupported-claim rate, action correctness, and the future model/memory cases are not complete. | PARTIAL — worktree evidence is not frozen capability |
| G — Reliability & Quantification | P8.4 records `PASS_WITH_FINDINGS` for durable FakeModel crash/resume boundaries. Failure, recovery, verification, durable reuse, and ambiguous-outcome tests exist. | No general production automatic retry is accepted; provider retry remains outside accepted capability. No accepted real-provider reliability benchmark, recovery-rate report, duplicate-invocation benchmark, latency/token baseline, or honest before/after comparison exists. | PARTIAL |

Supporting facts:

- Scanner v1 is independently accepted on synthetic fixtures and remains the
  trusted source inventory boundary.
- [P8.4](../p8_4_durable_model_loop/SPEC.md) is durable FakeModel-loop evidence,
  not proof of real-provider production execution.
- [P8.5](../p8_5_real_provider_boundary/SPEC.md) supplies provider contracts and
  an adapter boundary; its task record explicitly says the provider has not
  been connected to the durable loop.
- [Agent Trajectory Evaluation](../agent-trajectory-evaluation/SPEC.md) is
  current uncommitted, experimental worktree evidence. Its 41 recorded cases
  (`23` executable and `18` `NOT_IMPLEMENTED`) are a useful Gate F/G
  foundation, not accepted or frozen capability, and do not close either
  portfolio Gate.
- The current worktree contains concurrent uncommitted Runtime/Retrieval and
  evaluation changes. V2 does not claim ownership or acceptance of them.

A historical repository-wide run recorded
`458 passed, 2 skipped, 8 failed, 7 errors`. This result was not rerun as part
of the Master correction/freeze. Its 15 non-passes were recorded as legacy
relation-evaluation absolute-path failures. It is historical evidence of a
known gap, not current verification or a green-suite claim.

## 9. Portfolio Definition-Of-Done Gates

There are exactly seven Gates. Each acceptance decision has one primary
ownership concern; adjacent Gates may share evidence, but shared evidence does
not transfer acceptance ownership.

| Gate | Primary acceptance owner |
|---|---|
| A — Real Model | Provider boundary |
| B — Real Agent Loop | Model-driven Agent Loop |
| C — Retrieval + Memory + Context Assembly | Context contract |
| D — Business Vertical Slice | Business semantics |
| E — Human-In-The-Loop | Side-effect/HITL safety |
| F — Agent Eval | Evaluation correctness |
| G — Reliability & Quantification | Fault/recovery quantification |

### Gate A — Real Model

One real provider must drive the accepted production Agent path through a
provider-neutral boundary.

Completion requires:

- a provider-neutral request, response, action, usage, metadata, and error
  contract;
- one direct real-provider adapter with offline fake-client/recorded-response
  tests;
- normalized authentication, invalid-request, rate-limit, timeout,
  unavailable, malformed-response, and ambiguous-outcome errors;
- a production durable-loop integration that consumes `ModelResponse` without
  creating a second runtime, ledger, or permission layer and without assuming
  an automatic retry system;
- LinkLoom, not the provider, remains the only executor of local tools;
- credentials remain environment-only and absent from artifacts, traces,
  fixtures, errors, and committed configuration;
- cloud-sent evidence is bounded and visibly disclosed;
- at least one separately approved, budget-capped synthetic real-provider run
  records model identity, requests, usage, latency, and safe outcome.

The currently accepted capability does not include general production
automatic retry. Provider retry remains outside Gate A's accepted baseline;
failure, durable recovery, and ambiguous outcomes must be handled without
claiming an automatic retry policy that does not exist.

A real adapter that is only unit-tested or disconnected from the production
path does not close Gate A.

### Gate B — Real Agent Loop

The accepted production path must be:

```text
model -> ToolCall -> ToolRuntime -> ToolResult -> model -> ... -> final
```

The model must decide:

- whether a tool is needed;
- which allowed tool to call;
- the exact arguments;
- whether the observation is sufficient;
- whether another evidence step is needed;
- when to stop with a final result.

Completion also requires:

- every local tool call passes through ToolRuntime validation, permission,
  budget, ledger, checkpoint, and safe-error boundaries;
- multiple calls, malformed calls, undeclared tools, repeated calls, loops,
  premature finals, and step-budget exhaustion fail safely;
- a test proves that changing a ToolResult can change the model's next action;
- a production E2E trace shows at least one multi-turn tool sequence and one
  direct final path;
- crash/resume reuses durable responses and terminal ToolResults without blind
  provider or tool replay.

A fixed `search -> read -> return` Python workflow does not close Gate B even
when it uses ToolRuntime.

### Gate C — Retrieval + Memory + Context Assembly

The production model-visible context must have an explicit bounded contract:

```text
System Instructions
+ Tool Definitions
+ User Query
+ Retrieved Current Evidence
+ Relevant Controlled Memory
+ Previous Tool Observation
= Model Context
```

Completion requires:

- every evidence and memory item retains a source reference, version/hash, and
  trust/status metadata;
- current verified evidence has higher authority than historical memory;
- stale, revoked, expired, or conflicting memory cannot silently override
  newer evidence;
- context selection, ordering, deduplication, truncation, and token/size budget
  are deterministic or explicitly versioned;
- no-evidence and evidence-conflict behavior is explicit;
- an ablation or paired test demonstrates that retrieved evidence and relevant
  memory can affect model behavior for the intended reason;
- cloud disclosure states exactly which selected context leaves the machine;
- full runtime state, private absolute paths, secrets, Gold labels, and hidden
  evaluator data never enter model context.

Separate RAG and Memory modules that do not influence the production model
request do not close Gate C.

The request contract's existing `previous_tool_call` field is only a
foundation. `SingleAgentModelLoop` does not yet populate or use it in the
production provider flow; activating it belongs to M0.2, not to the accepted
baseline.

### Gate D — Business Vertical Slice

The first accepted business slice is `Team Decision & Action Agent` on a frozen
synthetic project corpus.

The corpus must contain:

- multiple dated discussions of one project decision;
- a newer decision that supersedes an older proposal;
- at least one rejected alternative and its evidence-backed reason;
- completed and unresolved action items;
- at least one contradictory, stale, irrelevant, and no-evidence case.

The accepted result must contain:

- `decision`;
- `rationale`;
- `rejected_alternatives`;
- `unresolved_items`;
- `next_actions`;
- `evidence`;
- explicit uncertainty and unsupported fields when evidence is insufficient.

Completion requires a real Agent Loop, not a summary script, and an E2E test
that checks result structure, evidence ownership, temporal precedence, no-
evidence behavior, and source immutability. No real employer or bank data may
be used as the acceptance fixture.

### Gate E — Human-In-The-Loop

After a grounded result, the Agent may propose a bounded action update through:

```text
proposal -> preview -> exact human confirmation -> permission
  -> backup -> apply -> audit -> rollback when required
```

Completion requires:

- reuse of the existing ChangePlan/permission/writeback implementation rather
  than a second write system;
- the proposal identifies which statements are evidence-derived and which are
  suggestions;
- the preview is per-file and matches the exact approved operations;
- approval names the plan identity, target root fingerprint/path, operation
  set, and digest;
- changed-source/stale-plan validation immediately precedes apply;
- backup is created before the first write;
- partial failure is visible and recoverable;
- audit and tested byte-for-byte rollback evidence exist;
- read-only commands cannot load or expose write-capable tools.

Portfolio acceptance may use only a synthetic fixture. Real-vault mutation
remains forbidden until canonical Milestone 6 is independently accepted and
the human separately approves the exact plan ID, vault path, and operation.

### Gate F — Agent Eval

The current 41-case trajectory record (`23` executable and `18`
`NOT_IMPLEMENTED`) is uncommitted, experimental worktree evidence. It may
inform the accepted evaluation design, but it is not frozen capability and
does not itself satisfy Gate F.

Evaluation must be separated into three layers.

#### Component Eval

- retrieval Hit@K and MRR or an explicitly justified equivalent;
- tool selection accuracy;
- tool-argument contract accuracy;
- memory retrieval/freshness behavior;
- evidence and citation validity.

#### Trajectory Eval

- unnecessary tool calls;
- missing required calls;
- malformed or denied calls;
- repeated calls and loops;
- premature final answers;
- checkpoint/resume safety;
- trajectory correctness and failure attribution.

#### Outcome Eval

- task success;
- decision accuracy;
- groundedness;
- rejected-alternative accuracy;
- unresolved-item recall;
- unsupported-claim rate;
- next-action correctness;
- safe proposal/writeback correctness where applicable.

Completion requires:

- a versioned, frozen synthetic regression dataset isolated from inference;
- deterministic metrics for contract-level behavior;
- optional judge results clearly separated from deterministic results;
- capability gaps reported as `NOT_IMPLEMENTED`/unsupported rather than scored
  as hidden success;
- bad-case records with stage-level attribution;
- a reproducible regression report with commands, config, model/provider,
  prompt/schema versions, and skipped checks;
- no change to Gold data merely to improve scores.

### Gate G — Reliability & Quantification

Reliability acceptance must cover at least:

- provider authentication/failure/timeout/ambiguous outcome;
- malformed provider response;
- tool validation, permission, budget, executor, and invalid-output failure;
- checkpoint-write failure;
- process crash and resume;
- stale source/evidence;
- partial writeback failure and rollback on a synthetic fixture.

The benchmark must report, where applicable:

- recovery success rate;
- duplicate provider invocation count/rate;
- duplicate tool execution count/rate;
- state and artifact consistency;
- end-to-end and per-stage latency;
- model and tool call counts;
- token usage and cost when legally available;
- task success and unsupported-claim rate under faults.

Completion requires at least one honest baseline comparison, such as lexical
versus hybrid retrieval or unrecovered versus recovery-enabled fault runs. The
comparison must be defined before measurement and retain raw run evidence.
Numbers may enter README or resume text only after reproducible runs produce
them. Gate G does not claim exactly-once execution, and it cannot close while
the required repository test boundary remains unexplained or red.

## 10. Gated Implementation Sequence

These labels sequence work; they do not replace the canonical product
milestones.

M1-M4 progressively assemble and prove one `Team Decision & Action` vertical
slice; they are not four separate demos. M1.1 alone does not complete Context
Assembly, business evaluation, reliability quantification, or the final demo.
The fully assembled evidence package is frozen only in M5 after the relevant
Gates independently pass.

### M0 — Production Agent Closeout

Purpose: finish the existing runtime/provider spine and then stop expanding
Runtime abstractions.

Ordered Child SPECs:

1. **M0.1 P8 integration repair** — repair and clarify only the current
   RuntimeEngine, RuntimeAgentAdapter, ToolRuntime, model-loop, and durable-state
   integration seams before M0.2. Diagnose current concurrent changes before
   editing; do not expand the architecture.

   M0.1 hard Non-Goals:

   - do not implement or redesign `GeminiProviderAdapter`;
   - do not add a new Provider;
   - do not add new Runtime abstractions;
   - do not perform RetrievalAgent model-driven migration;
   - do not implement Memory or Context Assembly;
   - do not implement Team Decision business logic;
   - do not expand Eval;
   - do not repair unrelated legacy evaluation failures;
   - do not redesign recovery or checkpoint behavior.

2. **M0.2 Durable real-provider loop completion** — adapt `ModelResponse` into
   the durable loop, populate and use `previous_tool_call` in the production
   provider flow, normalize provider failure, reuse durable responses without
   claiming general automatic retry, and complete one approved synthetic
   real-provider run.
3. **M0.3 RetrievalAgent model-driven migration** — replace the deterministic
   retrieval sequence with model-selected `search_notes` /
   `read_verified_note` / final decisions through the existing ToolRuntime.
4. **M0.4 Production E2E + resume acceptance** — prove direct-final,
   multi-tool, failure, crash/resume, trace, artifact, ledger, and no-duplicate
   behavior.

Exit: Gate A and Gate B pass independent review for the accepted production
path. Any new P9/P10 Runtime abstraction is stopped unless a later accepted
business SPEC proves it is necessary.

### M1 — Business Vertical Slice

Child SPEC:

5. **M1.1 Team Decision & Action vertical slice** — freeze the synthetic
   corpus and implement the structured, evidence-backed decision/rejected-
   alternative/unresolved-item/next-action result. The first acceptance path
   is read-only; an action proposal may feed the existing synthetic writeback
   boundary.

Exit: M1.1 may establish Gate D's primary business semantics. Gate E may be
credited only for the synthetic, human-confirmed path that independently
passes its own canonical safety gate. M1.1 alone is not a completed portfolio
vertical slice: Context Assembly, business Eval, Reliability, and final-demo
evidence remain M2-M5 work on this same slice.

### M2 — Context & RAG

Ordered Child SPECs:

6. **M2.1 Retrieval baseline + hybrid** — retain lexical retrieval as the
   baseline, add only the smallest justified semantic/hybrid improvement, and
   measure it on the same Team Decision & Action business dataset.
7. **M2.2 Memory + Context Assembly** — make selected memory and current
   evidence model-visible with precedence, stale/conflict, disclosure, and
   budget rules.

Exit: Gate C passes and its relevant Gate F component metrics are reproducible.
Do not create a separate RAG demo repository.

### M3 — Agent Eval Harness

Ordered Child SPECs:

8. **M3.1 Agent evaluation dataset** — version the same vertical slice's
   business regression set and isolation rules.
9. **M3.2 Agent trajectory/outcome metrics** — extend accepted trajectory
   foundations to the model-driven and business outcome cases.
10. **M3.3 Regression report + bad-case analysis** — emit reproducible metrics,
    capability gaps, and stage-level failure records.

Exit: Gate F passes without hiding unsupported cases or inventing scores.

### M4 — Reliability Benchmark

Ordered Child SPECs:

11. **M4.1 Failure injection benchmark** — exercise the same Team Decision &
    Action path through the existing checkpoint, ledger, recovery, trace, and
    writeback safety boundaries under defined faults.
12. **M4.2 Reliability result report** — publish recovery, duplication,
    consistency, latency, call, token/cost, and task-outcome evidence.

Exit: Gate G passes. Do not expand the recovery subsystem unless a measured
failure case requires it.

### M5 — Final Evidence Assembly

Ordered Child SPECs:

13. **M5.1 README / Architecture / Demo** — freeze a reproducible setup,
    architecture diagram, end-to-end demo, measured results, known limits, and
    truthful capability matrix.
14. **M5.2 Resume / Interview Story** — derive resume bullets and a 45-minute
    technical narrative only from accepted code and measured evidence.

Exit: all seven Gates are complete and independently accepted. Portfolio text
must not claim enterprise deployment, production scale, exactly-once behavior,
or metrics not proved by repository evidence.

M5 assembles and demonstrates the single vertical slice developed through
M1-M4. It creates no eighth acceptance Gate and no new product capability.

## 11. Portfolio Freeze — Final Step, Not A Gate

Portfolio Freeze is a separate final evidence step after the seven Gates; it
is not Gate H and must never be counted as an eighth Gate.

The current **ACCEPTED AND FROZEN — Planning Baseline V2** status freezes this
planning contract only. It does not mean the future M5 Portfolio Freeze has
occurred, and it completes no Gate.

The freeze may occur only when M5 has assembled the one Team Decision & Action
vertical slice, all seven primary Gate owners have independent acceptance
evidence, and the resulting demo, architecture, measurements, limitations, and
interview claims are reproducible. Freezing packages accepted evidence; it
does not retroactively complete a Gate or convert experimental worktree
evidence into accepted capability.

## 12. Canonical Product-Roadmap Crosswalk

| V2 execution slice | Canonical product milestone relationship |
|---|---|
| M0 Production Agent Closeout | Cross-cutting implementation foundation for `Can Find` and `Can Act`; not a user milestone. |
| M1 Business Vertical Slice | Primary vertical through `Can Find` and `Can Act`; action output remains a proposal. |
| M2 Context & RAG | Improves `Can Find` and evidence continuity in `Can Act`. |
| M3 Agent Eval Harness | Acceptance evidence across the relevant canonical milestones. |
| M4 Reliability Benchmark | Cross-cutting trust evidence; synthetic writeback faults map to `Modify Only After Confirmation`. |
| M5 Final Evidence Assembly | Documentation and evidence freeze for the one M1-M4 vertical slice; no new product capability or eighth Gate. |

Any actual apply behavior belongs to canonical Milestone 6 regardless of which
V2 slice proposes the action.

## 13. Child SPEC Contract

Every new requirement under this Master must include:

- **Parent Product Milestone** — one canonical milestone from
  `docs/PRODUCT_ROADMAP.md`;
- **Parent Execution Slice** — one M0.1-M5.2 item above;
- **Master Gate** — one or more of Gate A-G;
- **Problem**;
- **Current Baseline With Evidence**;
- **Scope**;
- **Non-Goals**;
- **Inputs And Outputs**;
- **Safety / Permission / Privacy Boundary**;
- **Exact Production Files**;
- **Exact Tests And Fixtures**;
- **Acceptance Criteria**;
- **Required Reviewer Evidence**;
- **Interview Evidence**;
- **Learning Objective** — no more than two concepts;
- **Stop Rule**;
- **Dependencies And Human Approval**.

If a task cannot identify the Master Gate and canonical user outcome it
advances, it must not be implemented.

Child SPEC order is fixed by Section 10. Reordering requires a documented
dependency problem and explicit human approval; attractive adjacent work is
not enough.

## 14. Non-Goals Until All Seven Gates Pass

- more Agent roles;
- a new multi-agent framework;
- SFT, DPO, reinforcement learning, or fine-tuning infrastructure;
- complex autonomous long-term memory;
- MCP for its own sake;
- broad multi-provider expansion;
- Kubernetes or distributed-platform work;
- large vector-database infrastructure;
- UI redesign;
- speculative P9/P10 Runtime abstractions;
- a separate RAG showcase project;
- real employer/bank data or implied enterprise deployment;
- real-vault mutation without the canonical Milestone 6 safety gate.

## 15. Stop Rules

Stop and return to planning or review when:

- a proposed task lacks an accepted Child SPEC and implementation plan;
- the next change does not advance a named Gate;
- an implementation needs files outside its approved boundary;
- a plan treats current dirty-worktree code as accepted evidence;
- a model, memory, or evaluation path would expose Gold, secrets, private
  absolute paths, or unapproved note content;
- a read-only command would load write-capable tools;
- a real-provider run lacks explicit credential, privacy, and budget approval;
- a real-vault operation lacks reviewed preview, exact approval, source-version
  validation, backup, audit, and tested rollback;
- a metric is proposed for README/resume use before a reproducible run exists;
- M0.4 passes and work attempts to continue Runtime abstraction rather than
  moving to the business slice.

## 16. Master SPEC Freeze Record And Acceptance Criteria

Independent Reviewer verdict: **`PASS_WITH_FINDINGS`**, with no blockers. The
findings were documentation clarifications and are incorporated in this
frozen planning baseline. They did not change the seven Gates, M0-M5 order,
product direction, or canonical six-stage product roadmap, and they completed
no Gate.

The independent acceptance criteria for this Planner artifact were:

- product positioning, the primary workflow, seven Gates, execution order,
  Child SPEC contract, Non-Goals, and Stop Rules are explicit;
- every current-state statement points to repository code, tests, task records,
  or Git evidence;
- planned behavior is not described as implemented;
- the deterministic RetrievalAgent is not mislabeled model-driven;
- the Gemini adapter is not mislabeled production-connected or network-tested;
- memory references are not mislabeled model-visible context;
- synthetic writeback foundations are not mislabeled as real-vault approval;
- trajectory evaluation is not mislabeled complete business outcome
  evaluation;
- P8.4/P8.5 limitations and the red full-suite record remain visible;
- M0-M5 are clearly subordinate to the canonical product roadmap;
- the old Master SPEC is preserved as history but not presented as an equal
  active roadmap;
- no production code, tests, fixture, Gold data, credential, real vault, Git
  commit, push, or PR was changed by this Planner task.

Documentation checks:

```powershell
git diff --check -- docs/requirements/linkloom-master
rg -n "Gate A|Gate G|M0.1|Team Decision|North Star" docs/requirements/linkloom-master
git status --short
```

Product tests are not required for this documentation-only Planner change.
Their latest recorded status must still be reported rather than silently
treated as passing.

## 17. Next Boundary

The only authorized next task is **M0.1 P8 integration repair**. Its first
action is to write and approve the M0.1 Child SPEC and implementation plan;
this frozen Master does not create those artifacts and does not authorize the
Worker to begin M0.1 code.

## 18. Learning Reflection

### Step

- Role: Planner
- Feature: LinkLoom Portfolio Completion Master V2
- Files reviewed: repository contracts, existing linkloom-master planning,
  P8.4/P8.5 records, Agent trajectory evaluation records, and current
  Runtime/Agent/Tool/Memory/Evaluation/Mutation source boundaries.

### What Changed

The portfolio target is now organized around one business vertical slice and
seven objective completion Gates rather than a list of disconnected Agent
mechanisms.

### What I Learned

1. The repository is materially ahead of the old 2026-07-12 Master baseline,
   but a real-provider adapter, durable FakeModel loop, and deterministic
   RetrievalAgent are three different completion states and must not be
   collapsed into “real production Agent.”
2. Memory and safe writeback foundations are reusable, but Memory is not yet
   part of model-visible context and writeback is not yet part of the Team
   Decision & Action workflow.

### Evidence

- P8.4 records durable FakeModel-loop `PASS_WITH_FINDINGS` evidence.
- P8.5 records offline provider-contract/adapter evidence and explicitly states
  no Gemini-to-loop connection or network smoke run.
- Current RetrievalAgent source is deterministic and current Memory injection
  returns references after execution.
- Current mutation modules and tests establish a synthetic-only HITL
  foundation.
- Current uncommitted, experimental Agent trajectory worktree evidence records
  41 cases, 23 executable and 18 `NOT_IMPLEMENTED`; these counts are not
  accepted Gate F capability.

### Next Step

The Master SPEC is accepted and frozen after independent Reviewer
`PASS_WITH_FINDINGS` with no blockers. The only authorized next task is to
draft M0.1's Child SPEC and implementation plan for separate human approval;
no Worker implementation starts automatically.
