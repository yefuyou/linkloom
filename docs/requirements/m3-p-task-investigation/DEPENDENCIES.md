# Dependency audit: M3-P onto master

Baseline master: ca2e775ce17581f8b69b2e1f9daf813a1230fb1c. Selected source: dd76a25bd9e70544c8e0534538b694015928fea1. Both remote heads were re-read with git ls-remote. No history merge or old development commits are imported.

| Layer | Master | Selected integration | Acceptance boundary |
|---|---|---|---|
| M3-J / F | No task observation discovery | TaskProposal, TaskObservation, bounded discovery and exact source validation | Deterministic host contracts plus FakeModel; no general semantic acceptance |
| M3-K / G | Existing bounded source-relation TaskPlan only | Existing typed task InvestigationPlanner with endpoint/dependency/scope admission | Model plans remain proposals; Host rejects invalid prerequisites |
| M3-E | Existing ContextAssembler and bounded history | Working Context, atomic evidence groups and fresh-original recovery in the same ModelLoop | No second loop, no summary-as-evidence; explicit context budgets |
| M3-I | Existing Unified Ask/Runtime/Reviewer | Task workflow and Host source bridge | Previous I was scripted Host wiring, not normal real-model product acceptance |
| M3-N | Absent | Optional source-bound discovery context library, default absent | No new LIVE run or task-profile default |
| M3-O | Absent | No AB runner, score/checkpoint platform or profile default | Historic 13 pairs are not current mainline scores; Profile B product opt-in deferred |
| M3-P | Absent | Observation/proposal/P4 presentation and saved-result revalidation | Exact current Evidence Catalog membership remains mandatory |
| M2 P1/P2/P3/P4 | Absent | Existing finite proof functions, immutable sidecar and read-only replay | P4 product reads never write or impersonate human approval |

M1 candidate task payload persistence is included because TaskObservationQuery and P2 reviewed-change readback require it. It extends the existing candidate/receipt path; it creates no second task authority. Existing master Recovery/progress semantics are retained; unrelated old Final-correction and adaptive-planning patches are excluded.

## M2 failure remains FAIL

[Original M2 delivery](https://github.com/yefuyou/linkloom/blob/dd76a25bd9e70544c8e0534538b694015928fea1/docs/requirements/m2-integration-acceptance/DELIVERY.md) explicitly records H01-H04 **0/4**, recall 5/8, revision 0/5 and the H04 initial +2/history sequence failure. H01 missed revision wording, H02 missed change/negative bridges, H03 missed positive revision evidence and returned REFUTED instead of CONFLICT, H04 missed revision/correction bindings. No new holdout is run and none of these failures is declared fixed.

[Separate P4 review](https://github.com/yefuyou/linkloom/blob/dd76a25bd9e70544c8e0534538b694015928fea1/docs/requirements/task-relation-lifecycle/REVIEW.md) accepted bounded explicit-input store/query/lifecycle replay, including source/endpoint integrity, terminal lifecycle, dependency retraction and current competing-observation checks. It did not accept M2 semantic generalization or future Host wiring. This integration re-verifies that finite read contract; missing/negative/unknown/stale/retracted records cannot unlock task steps. The API-4 four-day example uses its original explicit finite proofs, not the failed H04 claim.

## Product reachability

A: Normal UI/HTTP exposes the task domain, current capability state and strict unavailable/blocked behavior; existing reviewed-state and source-relation features retain their permissions. Saved task reads use existing Runtime artifacts and current authorization.

B: An explicit offline task demo starts the same Product UI/HTTP with public synthetic meetings and deterministic FakeModel stages. HTTP acceptance uses actual routing, jobs, ToolRuntime, durable Ledger, Grounding and Reviewer. This is an executable product wiring demonstration.

C: Real task-stage Provider composition and authenticated task relationship confirmation are not enabled here. Existing general provider flags do not grant task-stage authority. No new LIVE/costed request or countTokens call is made.

## Excluded

Old evaluation platforms, M3-O AB/checkpoint runners, Canary LIVE grants/ledgers, adaptive source planning, historical development commits and bulk M3-P evidence directories are excluded. Three small immutable public semantic replay references are retained solely for existing E/Reviewer replay contracts, with original provenance and zero new Provider calls. Gold and frozen baseline Ledgers are unchanged.
