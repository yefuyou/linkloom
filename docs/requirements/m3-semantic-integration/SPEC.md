# M3-D semantic integration SPEC

The user task is the approved boundary. Base: 26392432dcef70692461e6f2f50bc1bd5528f69a.
Integrate A bd5de920e4bcf1a76c12253c7001910552baf287 and B
8453126d6c4eef386727e193f39df461e0816b2a with ordinary Git history in this
new worktree/branch only. Both inputs have the exact requested common ancestor;
their changed file sets do not overlap. B was fast-forwarded, then A merged.

## Interface differences and minimal architecture

A SemanticContext requires complete competing M1 inventory, authorized immutable
source versions, typed endpoints, exact spans, revocations, queries and limits.
B inspect output is a bounded display projection and cannot supply this proof.
Add a host-owned builder reading the original authorized snapshot; never consume
the display projection as complete evidence. Missing/oversized inputs are BLOCKED.

A propose_task_relations is a forced Provider output function, not an executable
Runtime tool. Add analyze_task_semantics under the existing ToolRuntime, opt-in
for task_relations only. Its model arguments contain authorized workspace and
investigation ID only; no secrets, paths, arbitrary source or model choice.
Add a small explicit call-bound executor adapter to ToolRuntime so the inner
request can be correlated to the existing outer ToolCall identity.

Use A SemanticProposer and validate_proposals without rewriting their semantics.
Durable exact-request reservation and response/result artifacts live in the
existing ModelArtifactStore, separate from P4. One authorized semantic batch per
run; independent inner input/output/request/cost admission. Reserve before the
adapter, save the response before returning, reuse a completed receipt, block an
ambiguous reserved attempt. Never automatically retry a charged request.
No new live Provider execution is enabled; only explicitly injected FAKE/REPLAY
adapters are used in this scope. Imported historical usage is not new billing.

The original Coordinator -> RetrievalAgent -> SingleAgentModelLoop -> ToolRuntime
continues to own outer budget/checkpoint/recovery. Allow its configured step
budget for task_relations (inspect, P4 query, semantic analysis, Final). Final
selects observed receipt identities; Reviewer re-reads the raw snapshot, artifact
identity, exact citations and lifecycle. Stale/revoked sources block release.

## Result and evidence boundary

Keep MODEL_PROPOSED, EVIDENCE_VALID/INVALID, SEMANTIC_NOT_EVALUATED and publishable
separate. All model proposals remain semantic NOT_EVALUATED and publishable=false.
Valid proposals can be displayed as pending model analysis; P4 current results
remain a separate field. Conditional date comparisons never establish a business
revision or current plan; conflicts/incomplete proof block comparison.

Declare evidence coverage from the host's explicit investigation source inventory,
not Gold. Missing independent change evidence or explicit UNKNOWN investigation
sources is reported, never silently appended to model citations. New Dev variants
and controls test this boundary; frozen A artifacts/Gold/metrics remain untouched.

## Fixed acceptance

1. Real existing Runtime executes inspect -> query -> semantic -> Final -> Reviewer
   using FAKE or verifiable REPLAY; prove +4 conditional revision, three impact
   verdicts, conflict with BLOCKED date, and missing coverage disclosure.
2. Wrong workspace, stale/revoked sources/endpoints, missing evidence, incomplete
   or over-budget source inventory, Provider failure, inner/outer budgets and
   source changes at review fail safely.
3. Independent outer/inner identities, request counts, unknown cost/token fields,
   durable cached reuse and ambiguous-outcome blocking; no uncontrolled re-billing.
4. Preserve ask/connect/team_decision and P4 read-only behavior; no source/P4 or
   formal Decision Memory mutation. No ContextAssembler/Context Engineering edits.
5. Connect to the existing m3_task_impact Dev executor contract. Executed offline
   adapter results remain NOT_EVALUATED for business quality; remaining Dev NOT_RUN.
   No holdout execution, old Gold/scorer edits or frozen metric replacement.
6. Deliver real receipts/results/errors/coverage and reproducible sanitized local
   evidence, independent read-only review, exact staging, commit and normal push
   to codex/linkloom-m3-semantic-integration, verify remote SHA, then stop.

## Internal increments

Raw snapshot builder/coverage tests -> durable inner-call binding -> opt-in tool
and Final/Reviewer integration -> recorded/fake acceptance and Dev seam -> existing
regression checks -> independent review/fixes -> delivery/commit/push.
No new database, router, agent, P4 semantic receipt/promotion or semantic classifier.
