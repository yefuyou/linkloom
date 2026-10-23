# Historical trust addendum

This addendum is an offline audit of
`docs/evaluation/cloud-credit-sprint-20261023/evidence.zip` from historical
commit `f26191487f459d7af7016898422b9bbcfa1a5cee`.  The analyzer records the
ZIP SHA-256, every evidence member hash used by an attempt, the frozen source
SHAs from `final-manifest.json`, and its own source hash.  It does not create an
SDK client, inspect credentials, or make Provider calls.

The archive contains 58 reservations: A=16, B=21, C=10, D=6, E=5.  There are
57 local wire requests and 56 attributable successful Provider receipts.  The
trust classification is `SENT_SUCCEEDED=56`, `SENT_FAILED=0`, `NOT_SENT=1`,
and `OUTCOME_UNKNOWN=1`.  B's TLS `ConnectError` has a wire request but no
Provider response and is therefore `OUTCOME_UNKNOWN`; D's second attempt has
positive `NOT_SENT_PREWIRE` replay evidence and is `NOT_SENT`, while its
historical Session ledger remains `UNCERTAIN`.  Missing usage never implies
free service: actual billing remains `NOT_VERIFIED` and reservations remain
retained.

Six early `campaign_support.py` declarations are missing their source bytes:
`ce-b-20261023` declares `a2b78510…db352`, and
`ce-b-20261023-tls-recheck`, `ce-b-20261023-unattempted`, `ce-c-20261023`,
`ce-c-20261023-resume`, and `ce-d-20261023` declare
`4710b844…e2a4`.  The archive only has final
`work/cloud-credit-sprint-20261023/campaign_support.py` (`42c43010…ff65`), so
all six historical source identities remain `NOT_VERIFIED`.  The initial D
adapter declaration `9af19e3b…cde6` likewise differs from archived
`independent_model.py` (`23e50697…ceceb`).  Legacy hard-coded
`unresolved_provider_outcomes=0` locations are emitted as evidence, never used
for the current count.

The historical AMI F run used 28 successful calls and produced 75 claims:
FACT 18, PLAN 5, QUESTION 9, PROPOSAL 23, OPINION 14, SPECULATION 1,
DECISION 5, ACTION 0.  Five task candidates had `Owner=null`/UNKNOWN;
Discovery accepted 0 valid and 0 partial tasks and rejected all five PLAN
candidates: four `NON_ASSERTED_TASK`, one
`TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS`.  The clay-model task was
found in ES2005c chunk 17 but rejected by the apostrophe quote-scope filter.
The industrial-design assignment was missed; the UI/interface case was an
incomplete cross-turn fragment; the marketing role/work candidate failed exact
grounding; and the speech-recognition decision was not emitted despite an
explicit “definitely going for” utterance.  Other observed decisions included
the latex/rubber shell and multiple-shape public choice.  These are
`DEV_DIAGNOSTIC` observations, not semantic Gold scores; independent review
remains `INDEPENDENT_REVIEW_NOT_VERIFIED`.
