# Independent read-only review

Reviewer: a separate `gpt-5.6-luna` agent with max reasoning, with no implementation
ownership, reviewing this isolated worktree against the M3-P SPEC and the
code-review-and-quality skill. No real Provider or LIVE ledger was used.

## First review: REQUEST CHANGES

One P2: `ProductTaskSources` rebuilt an evidence registry from source text, and
saved-result reconstruction did not consult the current product evidence
catalog. Removing one synthetic `ll_source_evidence` row while preserving source
content/version allowed a cold read to regain supported identity/revision and a
completed comparison. Source update/delete tests did not cover this narrower
evidence authorization revocation.

No other P1/P2 was reported. Reviewer checked endpoint/predicate matching,
workspace/source/lifecycle, comparison premises, no model promotion, no human
authority claim, no automatic F/G resume, and frozen-file boundaries.

## Repair

The existing ProductTaskSources bridge accepts the product current-evidence
reader. Both hot preparation and cold reconstruction bind it to the current
Workspace's `ProductRepository.current_evidence`. Construction and every
current-source validation require original spans' exact source/version/reference,
content/quote hashes, line range and quote to remain in that authoritative catalog.
Text retention alone cannot recreate revoked membership.

RED: new source-catalog unit test failed before implementation. GREEN: new
catalog-membership test and existing P4/source tests 10/10 passed. The Product
HTTP regression removes a synthetic evidence row without changing source text
or version, then requires both hot read and separate-process cold read to return
BLOCKED with no quotes, trace or task review. That regression passed 1/1.

The first attempted HTTP RED command was not executed: automatic approval
review could not finish due to account usage limits. No bypass was used. The
user requested continuation, the unit RED ran in the ordinary sandbox, and the
later required HTTP run was approved normally.

## Independent re-review: PASS

The separate Reviewer confirmed the P2 closed, with no open P1/P2 findings.
It checked exact membership identity, validation at construction and every read,
both production callback injections, the repository active-source/current-version
join, and no regeneration. Its independent focused unit/host/P4 run passed
10 tests. The reviewer relied on the main execution record for HTTP regression
evidence, and made no LIVE or real-ledger access. Protected frozen paths and
`git diff --check` were clean.

Final repair-regression counts and post-fix HTTP artifacts are recorded in
`evidence/verification.json`. This is an offline implementation acceptance;
real-model semantic correctness, CI and deployment are not claimed.
