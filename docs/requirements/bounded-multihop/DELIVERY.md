# Bounded source-relation investigation delivery

Base b2cbe030b14a1dead7f80087df0563a52f0166f7. Branch
`codex/linkloom-agent-generalization`. Implementation was committed as a505962
before opening the independent heldout results. No real Provider was called.

## What actually executes

RuntimeEngine.start_multi_agent with team_decision owns the genuine ModelLoop,
ToolRuntime, immutable source identity, Tool Ledger and Reviewer. An explicit
RunRequest.task_plan is used directly; template mode falls back to the old
plan_team_decision_query. Model planning calls propose_task_plan, validates the
answer-free plan and permits one bounded repair. That path is distinct from
our new deterministic, opt-in plan_relation_investigation compiler. The FakeModel
controller selects real search/read actions from observed results; it receives
no expected answers and does not stand in for a learned planning model.

RuntimeAgentAdapter._planned_search_query reconstructs parent search/read pairs
from the durable ledger, verifies typed relation objects, then uses the existing
SubgoalQueryStrategy. A retrieved hit alone is insufficient. ToolRuntime checks
supplied source_context identity and allowed tools (some identity fields remain
optional in the existing API); this controller supplies the complete identity.
read_verified_note accepts only
an earlier search-issued evidence ID, rechecks the indexed file version and
quote hash, and fails when the source changes. The isolated fixture loader
indexes only the selected workspace; injected foreign source context fails
before tool execution. Forged downstream entities fail before retrieval.

Existing grounding recognizes aliases, owners, direct leaders, replacements,
approvals and limited dates. TaskPlan/v2's declared directed type vocabulary is
narrower: alias_of_project, owner_of_project, reports_to; only the explicit
alias+owner combined path was previously permitted. This slice uses separate
single-edge subgoals, two or three hops total. The source grounding change filters
reports_to by an explicitly requested snapshot date, matching the existing
owner rule. It never infers validity between snapshots.

Independent review reproduced two Final gaps: search-only citations could support
an unrelated claim, and an unrelated main answer could accompany a correct
rationale. validate_relation_final now requires actual verified reads and binds
every atomic claim to the declared hop's relation, subject, verified target and
date. Complete main answers must cover every hop. This guard applies only to the
two declared TaskPlan/v2 relation profiles. Targeted independent re-review found
no remaining safety blockers. Each demo execution also creates fresh vault,
index, runtime and trace directories, preventing output-root reuse from retaining
another workspace's files.

Unified Ask remains unchanged. It discovers one approved decision slot, pins
subject/relation/date, and uses start_temporal with an authority-bound validator.
Its source-relation vocabulary does not grant multi-slot decision authority.
Ordinary relationships therefore run through the standalone source investigation
entry, using the existing team-decision-result/v1, never the temporal proposal
contract. Runtime `approved` means evidence/Reviewer acceptance here, not human
business authorization. No formal Decision Memory is written. Partial output
keeps a null main decision and independently grounded known facts in rationale.

## Frozen evaluation

Development: 11/11 task outcomes. Independent heldout: **8/10 (80%)**, first run
against the locked implementation; the query, documents, oracle and scoring
were not tuned after observing results. Heldout freeze commit: 3f85b27; SHA-256:
`cbfde841b517a3d2f78b59a14167386b725ae3d3de603ffab490b2bdef43152f`.
A post-freeze uncommitted draft was preserved in ignored work/ and the exact
committed freeze restored before implementation; that draft was never scored.

| Expected outcome | Correct / total |
| --- | --- |
| Complete chain | 5/6 |
| Partial known prefix | 2/3 |
| Refusal | 1/1 |

All 18 emitted citations refer to actual verified reads. Every dependent search
passes a separate audit against its preceding ledger prefix. No observed
retrieval, binding, relation-direction, Reviewer or run-interruption error in
this heldout run. Two task failures remain: HOLD-H's unseen mixed-language
wording returns planning_failed; HOLD-G safely keeps the verified owner and
stops, but classifies conflicting downstream reporting evidence as insufficient
rather than conflicting_evidence. Neither disputed leader is asserted. These
are real limits; the acceptance set was not rewritten to claim success.
After the Final safety repairs, replay still scored 8/10 with the same two failure
categories. That exposed-set replay is not a second blind evaluation.

The ordinary two-hop HOLD-A executes search(owner), read(q7.md), then
search(leader, owner=Elena Voss), then reads(q7.md,m4.md): 5 tools, 6 model turns.
Three-hop HOLD-B executes alias search/read(r2.md), owner search bound to
Copper Meadow/read(r2.md,u8.md), leader search bound to Jules Hart/read(u8.md,
v3.md,r2.md): 9 tools, 10 model turns. Both accepted Finals cite cross-file sources.

Across ten cases: 49 tool executions, 58 FakeModel turns, zero external requests.
Peak verified evidence context 2,653 JSON bytes; peak complete model-request
JSON 30,895 bytes; ContextAssembler peak estimate 166 tokens (rough local
estimate, not Provider tokenizer usage). Per-case elapsed time 0–1,101 ms.
Existing run allowance is 20 turns and the retrieval task retains its stricter
12-step allowance; search top-k=4, at most three declared hops, existing tool
and context limits remain. The demo adds a 30-second controller deadline
checked between turns; it does not promise interruption of arbitrary native I/O.

## Safety and local reproduction

- New plan/temporal and real Runtime attack tests: 14 passed.
- ToolRuntime, durable/Fake loops, original Unified Ask and Discovery: 102 passed.
- Focused TaskPlan/Grounding/planner: 68 passed, 30 history-dependent tests
  deselected. Before changes the unrestricted baseline run had 62 passed and
  29 missing-file failures for ignored phase9/11/14 artifacts. Those artifacts
  were not fabricated, copied or edited.
- Exact historical T03/T04 CASES/GOLD/complete trace bundle was unavailable;
  transport interruptions are not counted as completed wrong answers.
- Raw run results, Ledger and trace stay under ignored work/, not in Git.

```powershell
$env:PYTHONPATH='src;tests/integration'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
python tests/integration/run_bounded_multihop.py
python tests/integration/run_bounded_multihop.py --dataset tests/fixtures/bounded_multihop/heldout.json --output work/bounded_multihop/heldout
```

The heldout command intentionally returns nonzero for the two documented task
failures. Inspect its local report.json and Runtime artifacts for the actual
calls and source identities. FakeModel demonstrates execution and safety
contracts, entity-independent binding and bounded grammar; it cannot establish
real-model generalization to unseen questions. Production delta: 125 added/3
removed lines across four existing modules (planner, source grounding, TaskPlan,
retrieval agent); no dependencies, databases, new Final schema or Runtime
abstraction. The 206-line demo is specific to these source-relation contracts.
