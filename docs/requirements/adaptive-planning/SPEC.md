# SPEC: Adaptive source-relation planning

User authorization: Linkloom B request, 2026-10-16. Baseline `cd668f3`;
branch `codex/linkloom-adaptive-planning`. This request authorizes implementation,
synthetic evaluation, bounded Gemini calls, commit and push for this branch only.

## Problem and architecture

Unified Ask currently admits only `plan_relation_investigation`'s fixed grammar.
Gemini `propose_task_plan` / one `repair_task_plan` and Runtime validation exist,
but ProductInvestigationService requires exact equality with a rule plan.
The legacy RuntimeRunBackend can select model planning outside Unified Ask;
this is not product acceptance of adaptive source investigation.

Keep reviewed decision lookup and recognized relation compilation. An explicit
server feature flag admits unrecognized questions into model planning, after
workspace, date, decision-scope and per-request external permission checks.
The existing Runtime owns proposal, at most one repair, request accounting and
execution. A stricter source-relation validator runs in addition to the existing
TaskPlan validator. Persist and project the actual accepted plan, never a guessed
or prefilled downstream answer.

Allowed plans are exactly project -> owner -> leader (two subgoals) or alias ->
project -> owner -> leader (three). Root entities must occur literally in the
trusted query. Each later question uses only `{verified_entity}` and its immediate
parent. Questions use canonical relation forms, pinned exact dates, no alternate
free-text lookups, no discovery roots, branches, joins or repeated leadership hops.
Model refusal is an empty proposal which is rejected before tools, not a valid
TaskPlan. Source identity and workspace remain host-owned; source runs keep
Decision Memory and capture disabled. Existing Verified Read, Final, Grounding,
Reviewer and stale-source checks remain authoritative.

The simplest alternative is extending regexes; it cannot evaluate unfamiliar
wording without continually enumerating phrases. The model adds one proposal
request (at most one repair), latency and semantic interpretation risk. Structural
validation cannot prove arbitrary natural-language intent equivalence. Results
must distinguish rejection, interruption, partial evidence and actual completion.
No general Agent framework, new relation, arbitrary tool, real-vault mutation,
dependency, deployment, master/PR #3 change or old Ledger modification is in scope.

## Acceptance criteria

1. Rules and reviewed decision queries keep their existing deterministic paths;
   adaptive planning is configurable and defaults off until evidence justifies it.
2. Invalid/foreign-root/date-changed/extra-hop/answer-prefilled/write-capable plans
   are rejected before tools. Repaired plans undergo the same complete validation.
3. Model planning is included in the per-run Provider allowance. No permission
   means zero model calls; workspace changes cannot expose a prior run's evidence.
4. At least 60 new, diverse synthetic tasks have separate development, validation
   and frozen final acceptance partitions. Gold never enters model inputs.
5. The same tasks compare A rule planning, B validated adaptive planning and C
   simple retrieval summary when appropriate. Report routing, plan validity and
   rejection, completion, correct abstention, grounding, calls/cost, invalid tools
   and added latency; invalid plans never count as successful model planning.
6. Current Unified Ask HTTP and a synthetic browser demonstrate the full model
   plan -> Runtime -> Search -> Verified Read -> Final -> Grounding -> Reviewer ->
   UI path for at least one query the baseline rules cannot recognize, if Provider
   access permits. Otherwise report the exact blocker and preserve offline evidence.
7. Real runs use verified Gemini 3.8 Flash, independent append-only accounting,
   <=400 generations, <=$15 estimated cost, <=10800 seconds, serial tasks, zero
   SDK retries and <=3 independent trials per task. Stop after two consecutive
   strict uncertain outcomes. Preflight records identity, permission, compatibility,
   bounded cost, logging and timeouts. Preserve both successes and failures.
8. Independent review checks evidence and safety. Commit and push this isolated
   branch; leave integration approval to the user.

## Verification

Acceptance/contract tests first; focused regressions, existing Unified Ask HTTP
and DOM checks, full offline suite where practical. Dataset hashes freeze splits
before live evaluation; final partition runs after development is frozen. Live
models choose all runtime tools; scripted controllers are labeled offline only.

Reflection: the missing product boundary is plan admission, not new retrieval or
new permissions. Evidence will determine whether the optional path is useful.
