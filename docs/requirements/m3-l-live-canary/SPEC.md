# M3-L: Product LIVE Canary wiring and offline admission

Authority: the user's M3-L instruction approves implementation against remote
`df95bfb15b4af462fec062c218d04f0d84774e00`. It does NOT authorize any Provider
request. Historical release gates do not expand or supersede this specific scope.

Reuse the current Product Ask -> F -> G -> Runtime -> Semantic -> E -> Reviewer
chain. Add a closed Host composition for the frozen synthetic campaign only,
default disabled. HTTP cannot choose adapters, models, grants or budgets. Preserve
Fast Path and all existing semantic/premise/publication gates. No new execution
loop, database, memory, semantic authority or Vault access.

One durable Host request ledger covers F/G/Semantic/Runtime including repairs,
with stage/case/global atomic reservations before sending. Maximum 34 sends and
$3 application-side reservation, not a provider billing hard limit. SDK sends
once; no application retries. Unknown send/outcome/usage or timeout stops the
whole campaign without replay, including after process restart. Bind the frozen
manifest and source versions; changing the ledger location is not a resume path.

Freeze public A/B/C/D sources, three cases, common question, independent rubric,
model and budgets before execution. D contains three independently parsed
messages. No trusted task IDs or scoring answers enter model input.

Acceptance: offline tests exercise illegal adapter, retries, cross-stage and
concurrent budgets, restart with pending request, missing usage, unknown outcome,
revocation, bad citations/schema, unconfirmed premises and Fast Path. Preserve
raw usage/unknown fields and diagnose each failure. Tests deny external network.
Independent read-only review must close in-scope P1/P2 before commit and push.
Real semantics/business completion remain NOT_EVALUATED. Stop after delivery.

Implementation order: freeze/parser contract; durable budget contract; closed
provider factory and normal product admission; offline acceptance/regressions;
independent review, fixes, documentation, normal commit/push and remote readback.
