# Read-only diagnosis and interface boundary

Baseline: `2d8a21673f27804559b084e42c9ff828f3a9cac0`, verified against
`origin/codex/linkloom-m3-live-canary` before implementation. The new branch is
`codex/linkloom-m3-m-admission` in `work/linkloom-m3-m-admission`.

The first desktop-managed worktree request stalled before registration. A normal
Git worktree was created under the authorized workspace and used for all work.
The managed request later completed as an unused detached baseline checkout;
it was retained, since no deletion was authorized. The primary workspace and
its unrelated changes were not edited.

## Which rules decide admission

| Existing path | Shortcut or actual gate | Observed consequence |
| --- | --- | --- |
| `ui/unified_ask.py:classify_task` | Keywords choose reviewed state vs investigation; UNKNOWN blocks a matched decision | Unfamiliar wording is treated as unclassifiable even with useful context. |
| `_TASK_QUESTION` in `UnifiedAskService.ask` | Actual F/G admission gate | Task/deadline/deliverable words can start F/G without an investigative question; unfamiliar task wording misses it. |
| `ui/scope_discovery.py:discover_scope`, `_unexplained_terms` | Trust-preserving Fast Path identity gate | Unexplained natural language yields NO_MATCH. This is not evidence of no tasks or evidence of a relationship query. Keep the identity check. |
| Generic NO_MATCH + adaptive flag | Actual relation-domain gate | Every unknown question may become project/owner/leader planning, including greetings and task questions. |
| `runtime/team_decision_planner.py:plan_relation_investigation` | Bounded grammar compiler | A valid plan is a shortcut. None means this compiler did not understand the shape, not that the request has no legitimate investigation. |
| `runtime/adaptive_planning.py:validate_source_relation_plan` | Real capability/security gate | Two directed chains only, literal query-bound root, verified dependencies, exact snapshot date, current workspace/read-only. Keep these checks. |
| `ui/task_investigation.py:prepare_task_investigation` | F→G preparation and Host authority | F enumerates bounded candidates; G receives original question, version-bound source handles and typed endpoints. Proposals/refusals are not business authority. |

## Frozen failure reproduction

`tests/fixtures/m3m_admission.json` freezes 48 Chinese/English/mixed-language
cases, expectations and domain/capability conditions. The executable StartSpy
harness records actual router choices without executing a model. `before.json`
was written before production code changes; `admission-red.xml` independently
records 34 failed/14 passed assertions on that old router. Baseline counters:
2 false rejections, 31 wrong execution routes, 4/4 Fast Paths, 21 unnecessary
start boundaries. These are overlapping error categories, not model scores.

Cases include implicit work, changed dates, cross-message identity, project
relationships, reviewed decisions, greetings, unrelated questions, ambiguity,
misleading task/owner/current/supplier words, writes/foreign-workspace/prompt
injection, missing capability and stale/foreign context. Domain selectors are
explicit user assertions supplied equally to old/new implementations; the old
implementation ignores that new field. This is a public development set.

## Minimal repair and remaining blocker

Keep lexical discovery strict for reviewed decisions. Replace the global
unknown-to-relation fallback with an actionable domain clarification. Preserve
compiled relation plans, and narrow legacy task/source hints to explicit record
investigations. Do not add case-specific synonyms to intent or scope regexes.

The additive API accepts `investigation_context` with `domain` (`tasks` or
`source_relations`) and `snapshot_hash` from Ask config. This hash binds workspace,
current source IDs, versions and contents. The Host checks it under the current
workspace lock. It cannot select arbitrary sources, model, tools, grants or
budgets. The original question passes to existing F/G or adaptive planner.
Known formal queries still use the existing source-verified Fast Path.

Automatic semantic domain selection remains **INTERFACE_BLOCKED**: F has no
question-aware admission operation and runs before G; G assumes an already
chosen task capability, validates typed work, and cannot certify arbitrary goal
relevance or formal authority. The relation planner supports only two paths.
Adding a classifier or parallel Runtime would not repair these contracts within
this authorization. Explicit human domain selection supplies the missing scope;
it does not prove semantic relevance. G can still refuse after bounded F/G
calls. Arbitrary adversarial text/goal fidelity is not mechanically solved by
negative rules, source hashes or these scripted tests. Such refusals must not
be counted as successful semantic understanding.

No shared F extractor, G operation, Runtime, authority confirmation, Canary
adapter/grant/ledger/budget, frozen manifest or old evaluation was changed.

Worker handoff: baseline and observable route failures are preserved. Host
admission and the browser scope control are independently reviewable; real
semantic/business improvement is NOT_EVALUATED.
