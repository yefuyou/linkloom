# Unified Ask Product Experience

Approved boundary: the user request dated 2026-10-14; base ebc211555d98595faf73269e318eddb4d6ec2110.

One Product Ask form owns the selected workspace, explicit decision slot, optional valid-time date and question. A local, bounded capability classifier selects reviewed Decision Memory lookup or the existing temporal investigation service. Unknown tasks and missing scope ask for clarification. No model-based routing or guessed scope.

AS-IS: Product Ask -> POST /api/ask -> ProductApplication.ask -> keyword search + recover_decision. Timeline Agent form -> POST /api/agent/decisions/ask -> ProductInvestigationService.start_temporal -> snapshot -> RuntimeEngine -> scoped tools -> temporal Validator -> Reviewer -> projection. Ordinary Ask follows the global current workspace; Agent pins workspace/date/slot. Both use the same store; temporal setup repeats recovery for verification.

TO-BE: Product Ask -> POST /api/product/ask -> UnifiedAskService -> classifier + explicit workspace/slot/date validation -> ProductApplication.recover_decision OR ProductInvestigationService.start_temporal. GET /api/product/ask/runs/<id>?workspace_id=<id> returns the same result envelope. ProductApplication verifies receipts/evidence; existing Runtime validates read evidence and Reviewer accepts Agent finals. UnifiedAskService adapts only accepted finals and guards current workspace on polling.

Result v1: question, workspace_id, scope, path DETERMINISTIC/AGENT (null before classification), route intent/reason, status COMPLETED/INVESTIGATING/NEEDS_SCOPE/FAILED/PROVIDER_UNCERTAIN, verified conclusions, evidence, receipts, uncertainty, agent_run_id, actual Runtime Reviewer disposition (review_id, reviewer_agent_id, decision, checks, reason, created_at; rejected reviews retained), timeline, progress and safe error category. Deterministic results never claim an LLM Reviewer. Interrupted/running Agent evidence is never a final answer.

Compatibility: both existing Ask APIs and /legacy-ask retain their semantics. New UI uses only the new endpoint. The new automatic-routing endpoint requires both server capability and per-request external-provider opt-in. The existing /api/agent/decisions/ask endpoint remains an explicit Agent request under its prior server-level opt-in (--allow-temporal-agent requires --allow-external-provider); its legacy request format stays compatible. Old /api/ask never calls a model. Offline transports are explicitly declared by service configuration, not inferred from a query.

Acceptance: current lookup without model calls; historical date agrees with reviewed valid time; replacement investigation executes existing tools/Validator/Reviewer; unknown cause stays unknown; proposal C never becomes current; unconfigured Agent is unavailable; uncertain Provider never exposes a draft; workspace/receipt/evidence rejection; stale response cannot overwrite workspace/date/question changes; legacy clients unchanged. Actual browser: current, historical, Timeline, receipt/source, Fake Agent, failed Agent, workspace switch. Offline-only fixtures and FakeTransport; no real vault/Gemini calls.

Non-goals: provider/network repair, new Runtime/Decision Store/planner/memory, auto-approval, large visual redesign, real data writes, unrelated test cleanup, release/merge.
