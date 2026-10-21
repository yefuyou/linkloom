# SPEC: Final evidence trust boundary repair

## Problem

The legacy Team Decision Runtime can approve a Final that cites only one side of a same-scope contradiction already read during the run, and can approve source claims after search without a successful read receipt.

## Scope

Repair the existing shared Runtime/TeamDecision final boundary on master. P1-A must evaluate same relation/entity/project and valid-time observations from the current run, including successful reads that the Final omits. P1-B must require every substantive source claim in the legacy Team Decision path to bind to a successful current-run read receipt. Preserve reviewed Decision Memory and deterministic lookup contracts.

## Non-goals

No new security framework, provider calls, M2 semantic redesign, PR #6 changes, generic regex contradiction detector, or blanket read requirement for tools/results that do not claim source facts.

## Acceptance criteria

1. Original E15 reproduces on master and PR #6 before this fix, with approved/evidence_sufficient and only positive citation.
2. Original E07 reproduces on master and PR #6 before this fix, with approved/evidence_sufficient and no read receipt.
3. After the fix, same-scope positive/negative reads cannot yield approved/evidence_sufficient when Final cites only one side.
4. After the fix, search-only source claims fail closed; search→read→Final remains accepted.
5. Different valid dates remain independently answerable; no-evidence abstention and legitimate PARTIAL remain valid. Workspace/source revocation and PR #6 FakeModel/P4 regressions remain covered.
6. No real Provider, countTokens, LIVE evaluation, Gold, Seal or Ledger mutation.

## Evidence

Keep original attack inputs, Tool Ledger, visible evidence, Final, grounding/reviewer and result artifacts for master and PR #6; record RED/GREEN JUnit and remote CI.
