# SPEC: Real-Agent Reliability, Security & Performance

Approved boundary: the user's 2026-10-15 sprint request, executed from
`c657e7e4bdace3b673292f740a57d080a7002a76` on
`codex/linkloom-real-agent-reliability`. This explicit authorization supersedes
the earlier V1 feature freeze only for this sprint.

## Problem and behavior

Measure whether the existing bounded investigation and authoritative decision
paths remain reliable with unfamiliar synthetic questions, hostile source text,
and 10/100/500 sources. Preserve the distinction between source relationships,
reviewed business decisions, deterministic planning, and model tool selection.

## Architecture and scope

Reuse Unified Ask, RuntimeEngine, ToolRuntime, current evidence verification,
checkpoints, Provider Ledger, Gemini adapter, and existing offline fixture
controllers. Evaluation code is a small repository-specific runner, not another
runtime or ledger. Public inputs and grading Gold have separate files and
interfaces. Freeze cases and grading hashes before acceptance execution.
Authoring-agent exposure is disclosed; synthetic acceptance is not a strict
human-authored blind test. Execute development cases before selecting at most
two reproducible production defects. Run frozen acceptance after code freeze.

No new product capability, framework, database, general cache, real vault,
historical record edits, merge, PR, or release. Existing source, workspace,
authority, reviewer, feedback approval and revocation boundaries remain intact.

## Real-provider contract

Only a verified Gemini model newer than 2.5, initially `gemini-3.8-flash`.
One provider task at a time, at most 60 generation attempts, at most USD 10
conservative reservation, and at most 90 minutes from the first live attempt.
SDK retries disabled; no automatic resend of an uncertain request. Fail closed
before sending if request/cost limits cannot be enforced. Usage accounting must
include thinking tokens; missing usage retains the conservative reservation.
Use new request identities and the existing ledger. Raw requests, responses,
credentials and local transport diagnostics stay in ignored `work/`.
Only synthetic question/source text may leave the machine. Canary two-hop
first, three-hop only after canary success, then selected negative cases within
remaining budget. Failure or unavailable credentials is reported faithfully.

## Acceptance criteria

1. Official engineering research with URLs/access dates and five concrete gaps.
2. Source audit identifies plan origin, context ownership, budgets, verified
   reads, permission/authority boundaries, and limits of local reviewer identity.
3. 24–36 new synthetic cases, development/frozen partitions, covering requested
   A–P categories with fixed texts, sources, Gold, refusal rules and identities.
4. Outcomes, evidence support, trajectory, efficiency and safety have explicit
   denominators. FakeModel and live evidence are always separated. Unsupported
   routing, correct refusal, runtime failure and Provider uncertainty are distinct.
5. A/B/C baselines use comparable inputs and goals; unsupported paths are N/A.
   Any unavailable live comparison or statistical uncertainty is disclosed.
6. Synthetic attacks exercise source authority, workspace, memory, permission,
   forged evidence and redacted diagnosis boundaries; model influence and actual
   unauthorized effects are scored separately.
7. Measure 10/100/500-source preparation, snapshot, scan, retrieval and repeat
   behavior; report observed memory and evidence validity without stale caching.
8. At most two production fixes selected from measured evidence, each with
   reproduction, before/after metrics and targeted regression coverage.
9. Relevant Unified Ask, multihop, temporal, grounding/reviewer, feedback memory,
   ToolRuntime, uncertain-provider and workspace regressions; independent review.
10. Chinese factual delivery including limitations, five interview questions,
    production line delta and reproduction commands. Inspect diff/secrets,
    commit/push only this branch, then verify remote SHA and clean checkout.

## Implementation plan

1. Establish isolated baseline and official research; audit runtime and security.
2. Freeze small synthetic public/Gold datasets and score development baselines.
3. Profile actual preparation/scanning; rank reproducible defects and select two.
4. Reproduce selected defects with tests, fix within existing modules, regress.
5. Freeze code, execute acceptance and bounded Gemini checks after offline preflight.
6. Independent engineering review, resolve relevant findings, report and push.

The main agent owns implementation and integration. Independent audit agents
write only their assigned new reports. A separate final review checks the diff
and evidence and does not silently fix it.
