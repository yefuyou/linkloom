# M3-S2: task recall and date-token protocol

Authorized boundary: the user's M3-S2 request; base
`bde9d9e2b88c2e69132cf48d1534dc28d4fca764`, branch `codex/m3s-task-recall`.

F must discover explicitly asserted work independently of whether its owner,
deadline, project or vendor is known. Missing fields remain null in a PARTIAL,
SUPPORTING_ONLY TaskObservation. Conditional work, suggestions, negation, quoted
history and background do not become tasks merely because they contain actions.
Uncertainty about a field must not be interpreted as uncertainty about the work.

Use a versioned F profile with the existing TaskProposal/TaskFieldBinding wire
contract. Separate a literal role quote, a source date token and the model's
business role. The date token excludes boundary words, retains source precision
and never borrows a year from metadata. Schema descriptions and a syntax pattern
guide generation; existing deterministic binding and calendar checks remain
mandatory, including on cold read. No keyword-based discovery or date repair.

Architecture: change F prompt/schema selection and Product's LIVE F profile
only. Existing projection admits null fields. Leave V1/V2 historical prompts,
serialization, hashes and frozen cases unchanged. Do not change G, Runtime,
SemanticAnalysis, P4, master, PR #6 or M3-S1. No new dependencies or PR.

Implementation order: audit original receipts -> fix independent cases before
requests -> contract regression -> new F profile -> offline regressions -> bounded
LIVE comparison -> Product admission/cold read -> independent review -> commit
and non-force push. The task request already authorizes this repair boundary.

Acceptance:

- Attribute original F04, date-format, projection and legacy-veto failures from
  actual receipts, retaining original hashes and response identities.
- Regression on all original M3-S cases plus independent fixed natural-language
  cases: report task recall, false recall, field false acceptance, correct UNKNOWN
  separately; missing observations are never correct UNKNOWN.
- Reject malformed/invalid/unbound dates, preserve partial precision and explicit
  role uncertainty; no new Chinese business regex or sample-specific rules.
- Demonstrate improved real F observations admitted through Product and revalidate
  source version/hash, EvidenceSpan, speaker, Workspace and cold reads without
  repeating the M3-S1 ambiguity-replanning acceptance.
- LIVE uses gemini-3.8-flash, SDK attempts=1, request-before-send reservations,
  at most 20 new requests/$5 reserved, cumulative $50 cap. Stop on FAILED or
  UNCERTAIN; no automatic retries. Account for concurrent S1 reservations too.
- Independent review has no outstanding P1/P2 before commit/push; read back SHA.
