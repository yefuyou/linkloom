# SPEC: AMI public human Gold retrieval integration

Status: implementation authorized by the user's 2026-10-22 request. Planner:
`ami_plan`; Worker: coordinator and `ami_adapter`; independent review pending.

## Scope and architecture

Original AMI NXT words/DA child references -> faithful, versioned source adapter
-> all same-meeting DA passages -> existing `BM25Retriever`, locally available
`DenseRetriever`/`HybridRetriever` -> real `ContextAssembler` (internally calls
`select_evidence`) -> sealed predictions -> separate official-link scorer.

The source adapter preserves native word text/IDs, speaker, meeting, relative
seconds, nontext events, original XML references, member/archive hashes, and
Unicode codepoint spans. Word text is joined with one explicit ASCII space;
XML layout whitespace is not speech. Canonical turn order is deterministic.
Complete-turn-priority batches obey 8,000 characters and 16,000 UTF-8 bytes;
oversized turns split mechanically. Ordered chunks reconstruct canonical
bytes, and word mappings resolve to original XML IDs/hashes. Reversibility
does not mean reproducing XML serialization.

Retrieval uses every same-meeting DA, including empty/nontext DAs; oversized
DAs become deterministic pieces. Large F-stage batches are exported separately
and are not retrieval units. No query-specific chunking or lexical tuning.

Custodian preparation reads official ACTIONS/DECISIONS and summlink references,
verifies the previous frozen qrels/query/corpus, and exports separate roots.
Prediction receives transcript passages and the fixed query payload only.
The original summary XML, sections, qrels, and links remain evaluator-only.
Query text is the declared summary-to-DA retrieval question; this is not an
extraction evaluation. The process enforces input schemas, records opened data
files, rejects Gold/raw paths, and blocks network connections. This is a local
process guard, not an OS security sandbox against malicious native code.

## Inputs, outputs, and isolation

- Official archive: `ami_public_manual_1.6.2.zip`, SHA-256
  `b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d`.
- ES2002a-d development; ES2005a-d previously exposed diagnostic series.
  This is series-disjoint project splitting, not AMI's official held-out set.
- Expected: 49,806 text words, 6,981 DAs, 56 queries, 51 eligible, 5 no-link N/E.
  Query counts differ by meeting. Unlinked utterances are not negatives.
- New output directories only: transcript input, fixed query input, manifests,
  evaluator-only qrels/provenance, predictions, context traces, scores/bad cases.
  No overwriting prior Gold, seals, ledgers, or reports.
- All reports are `PUBLIC_HUMAN_GOLD`, official-link retrieval metrics, not
  exhaustive semantic relevance recall or LinkLoom Product end-to-end accuracy.

## Acceptance and checks (fixed before implementation)

1. Verify archive/member hashes, native IDs/references, and all expected counts.
2. Contract tests: Unicode, discontiguous NXT ranges, relative/missing times,
   speaker boundaries, nontext retention, hashes, malformed references,
   deterministic chunks, both budgets, and canonical roundtrip.
3. Test Gold canary rejection and query/passage whitelists; predictions must
   not read evaluator or original archive data. Score only sealed outputs.
4. Preserve all 56 queries and five N/E, with explicit denominators. Official
   link Recall/Hit@1/5/10 and MRR@10; final-context Required Evidence Coverage
   and exact per-target missing/dropped/truncated evidence. No Gold hints to
   selection. Distinguish selection loss from later rendering loss.
5. Run actual LinkLoom BM25 and the existing locally available dense/hybrid
   model with no download. Missing dependencies/weights -> `NOT_RUN` + reason.
   Keep fixed settings, source/query hashes, model/config/code fingerprints.
6. Verify reference output seals and recompute reference metrics with the same
   scorer and cohort. Previous diagnostic reference Recall@5=.3009259259,
   Hit@5=8/18, MRR@10=.3381834215 is a reference, not a product result.
7. Produce 5-10 representative failures with native source evidence, actual
   top10, correct-meeting/text tests, chunk/selection/context loss, and
   evidence-backed possible causes. Do not tune on these failures.
8. Relevant existing retrieval/context/provenance regressions pass. Independent
   review verifies the diff and evidence. Report exact commit and CI status.

## Non-goals and permissions

No paid calls, Gemini, new model evaluations through providers, Planner/Runtime,
query rewriting, new ranker, Guard weakening, owner/deadline/cross-meeting Gold,
README score changes, historical artifact edits, real-vault writes, or existing
PR changes. Public Action/Decision extraction, F/SemanticAnalysis, relation
accuracy, citation entailment, and Product E2E remain NOT_EVALUATED. Local
independent branch and commit authorized; no push, merge, or publication.

## Failure policy

Reject source/hash/reference/schema/seal inconsistencies before scoring. Keep
N/E and failed cases. Dense/Hybrid unavailability is explicit, never replaced
by a fake embedder. No blind-test/generalization claim for exposed queries.

Planner reflection: approved boundary is now executable; next evidence is
contract tests and an offline same-cohort run, followed by independent review.
