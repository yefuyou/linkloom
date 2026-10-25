# Recorded M2 failures — offline trust-boundary regression

`m2_real_failure_receipts.zip` contains 18 formal successful responses from the sealed
`work/m2-relations-20261024` research experiment. It includes all nine inputs,
actual wire request bodies, original assembled responses,
proposals, response receipts, the pre-model provisional analyst reference and
the final analyst assessment. It excludes warmups, failed transport attempts and
Provider journals. No original experiment or ledger was changed. Raw SDK stream
chunks are retained in the unchanged local `m2_real_failures.zip`, outside the
commit: their opaque Provider thought signatures are unnecessary for regression
and must not be distributed.

The distribution archive's `manifest.json` records 84 original file hashes, checked during import
against the original `FINAL_SEAL.json` (SHA-256
`30d575676154b28f5ffb5e7cd19dbd14ee8d3b4a6659df2700da46c4408b92fd`).
All entries preserve original bytes. These hashes detect changes, not independent
truth or authorship. The frozen reference remains **provisional / not human Gold**.

The tests bind the exact recorded assembled-response text to each proposal and
audit all 79 original citations against exact source/version/native utterance IDs.
The original SDK streams were reconstructed during the prior local acceptance;
the distribution does not include them or claim to rerun that transport check.
This preserves the
R02 Flash taxonomy error and R07 Argon abstention failure; it does not rescore
models after the product patch.

The two `evaluator/` entries are this inspected diagnostic's provisional analyst
reference/assessment, explicitly NOT_GOLD. No held-out/evaluator-only Gold, private
vault content, credential, or Provider continuation signature is included.

## Schema boundary

| Research field | Product contract |
| --- | --- |
| `relation_type=NONE` | No such M3 verdict; no automatic conversion to REFUTED or UNKNOWN |
| `relation_type=UNKNOWN` | Not a complete typed M3 receipt; missing proof remains unknown in P4 |
| `REVOKED` | No production business-revocation predicate. P4 RETRACTED means withdrawal of a relation record, not cancellation of a business task |
| `AFFECTS` | Not equivalent to task AFFECTED_BY a typed change |
| `CONFLICT` primary type | Not equivalent to an unresolved conflicting verdict for every typed predicate |
| `requires_review=false` / confidence | No authority to waive product validation or write memory |

`tests/m2_real_failure_support.py` constructs new local Markdown envelopes from
the unchanged supplied native utterances. Envelopes retain native IDs and text,
but have new local offsets/versions and no trusted speaker or event timestamp.
The ingestion timestamp cannot supply the unknown AMI timezone or an effective
business date. This is an explicit test adapter, not a production AMI adapter.

Typed variants are adversarial contract tests: e.g. place R07's actual quotes
under a deliberately false SAME_TASK/REFUTED assertion, or omit R02's counter
quote and posit an untrusted SAME_TASK premise. They are labelled FAKE and are
**not native Provider runtime replays**. They demonstrate that exact quote checks
can pass while semantic acceptance stays NOT_EVALUATED, publishable=False, and
no P4 relation or business state can be created from that proposal.

R06 is a contradiction inside the later meeting, with an immediate correction.
Natural, unresolved pure cross-meeting conflict, business cancellation/restoration,
owner/date/location revisions, actual delay and transitive effects remain N/E.

Run with the repository's normal pytest environment:

```text
python -m pytest -q tests/unit/test_m2_real_failure_regression.py tests/unit/test_m2_semantic_currentness.py
```

The recorded regression forbids outbound socket connections. The separate
currentness regression uses existing synthetic P4 fixtures, not newly fabricated
model successes, to reproduce historical-negative verdict projection.
