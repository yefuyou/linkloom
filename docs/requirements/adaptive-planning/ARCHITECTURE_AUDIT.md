# Existing planning and product integration

Baseline: `cd668f36300e5bfae7e8c654a54359dc40627d33`.

| Boundary | At baseline | This change |
|---|---|---|
| `GeminiProviderAdapter.propose_task_plan` | Tool-free JSON proposal, general TaskPlan/v2 examples | Optional `source-relations/v1` prompt profile, canonical questions, explicit decline |
| `repair_task_plan` | One repair with structured validation feedback | Same profile and complete strict revalidation |
| `validate_model_proposed_plan` | Goal, bounds, placeholders, directed types, read-only checks | Preserved; additional product policy validates exact two/three-step chain, literal root and pinned date |
| `plan_relation_investigation` | Fixed English/Chinese grammar, used by Unified Ask | Unchanged deterministic fast path |
| `RuntimeEngine` | Already owns proposal/repair/request accounting for model mode | Optional strict profile; declined/uncertain admission gets distinct observable result |
| `RuntimeRunBackend` | Can select model planning outside Unified Ask | Exposes persisted actual plan and actual planning request consumption |
| `ProductInvestigationService.start` | Rejects plans unequal to rule compiler output | Explicit optional adaptive run; source policy disables Decision Memory and capture |
| `UnifiedAskService` | Unknown relation grammar refused before Runtime | Only after rule/approved-scope routing fails, optional adaptive entry, before any external call permission/date/workspace checks |
| Final/UI | Source projection requires Grounding and actual Reviewer | Preserved; UI adds validated model-plan origin and planning call count |

State ownership stays unchanged: Runtime owns the plan, tool ledger, verified
dependency values, Final and Reviewer. Product owns selected workspace, source
IDs/versions, current-source checks, decision receipts and the HTTP envelope.
The model never receives gold, downstream answer values in its plan, workspace
authority, new tools, new relations or additional reporting hops.

The optional route uses at most two planning calls and the existing maximum
12 Runtime model turns. Planning consumes its own explicit allowance inside the
14-request run envelope; it does not silently subtract the established Runtime
completion budget. All generations also pass the independent experiment budget.

Independent review caught an ambiguity fallthrough: a `DECISION_SCOPE_AMBIGUOUS`
result could enter source planning. A failing HTTP test reproduced it; the fix
allows fallback only for `NO_MATCH`. A separate source-request guard rejects
explicit mutation/foreign-workspace instructions rather than dropping them.
These safeguards do not claim arbitrary natural-language intent equivalence.

Pressure points exposed by evaluation are existing date-language overreach,
ambiguous source entities, tool dependency arguments, evidence-conflict handling,
and Final protocol/grounding failures. They are documented as failures instead
of weakening plan constraints or introducing a new Agent framework.
