# P1 Passage Coverage Audit

## Boundary

This note records the focused audit for frozen case `RAR-F-O` from
`tests/fixtures/real_agent_reliability/public_frozen.json`. The case has one
valid document, `o-hostile.md`, containing three lines: an instruction-like
source line, the owner relation, and the leader relation. Gold requires one
grounded result containing both `Sana Wu` and `Theo Gray`, with
`o-hostile.md` as the required source.

The audit keeps the existing `Search -> VerifiedRead -> Grounding` boundary and
does not read or mutate a real vault. Provider calls are not involved.

## Reproduction

The old frozen path was run through the existing C baseline with BM25. The
result was:

```text
outcome: partial
reason: evidence_insufficient
facts: [sana wu]
sources: [o-hostile.md]
search_count: 2
read_count: 2
verified_read_dependency: true
```

Both searches and both verified reads return the same passage identity:

```text
relative_path: o-hostile.md
line_start: 2
line_end: 2
quote: Sana Wu is the accountable owner of Hostile Delta.
```

The leader line at line 3 was never registered as a searchable evidence ID, so
the read tool and downstream grounding could not recover it. The focused
regression is [test_recovery_passage_coverage.py](/D:/webproject/Linkloom/work/linkloom-agent-recovery-optimization/tests/unit/test_recovery_passage_coverage.py).

## Root cause

`RuntimeRetrievalBackend.search` first obtains a document-level BM25 candidate.
For that candidate it calls the existing line-level `retrieve_evidence`, but
then materializes only one passage:

```text
passage = next(item for item in line_hits if item.source_kind == "note_body", line_hits[0])
```

The one-passage projection is at
`src/linkloom/retrieval_v2/runtime_backend.py` around lines 243–268. It was
designed to prevent a title or heading from hiding the best body line, but it
also collapses distinct relation spans from the same source document. In O,
the owner line wins the projection; the leader line is discarded before
`RuntimeAgentAdapter._search_notes` receives the result.

This is a coverage loss rather than an identity collision. The current
passage identity already includes workspace, source/document identity, content
version, line range, and quote hash through `passage_evidence_id`. Since the
second line never reaches `_decorate_evidence`, it never receives its own
identity. The subsequent `read_verified_note` and claim grounding layers are
therefore behaving consistently with the incomplete search result.

## Implemented minimum fix

Keep document ranking, source freshness checks, and all existing tool gates.
During materialization, the implementation preserves the ordered line hits for
each selected document, emits each exact passage through the existing
`_decorate_evidence` path, and applies the caller's `top_k` after global BM25
ordering. Non-BM25 modes stop materializing once `top_k` is reached. There is
no special query-prefix ranking rule in the final implementation.

The new canary exposed a second loss after candidate discovery:
`ContextAssembler` deduplicated evidence by `source_ref`, collapsing all
passages of one document. A leading introductory paragraph could hide the
relation lines even after the retriever fix. The final assembler deduplicates
by the existing opaque `evidence_id`; different passage identities survive,
while repeated copies of the same passage do not. Workspace checks, item/token
caps, scope checks and the separate approved-memory duplicate-source rule
remain. Tests cover distinct passages, duplicate IDs, caps and same-file noise.

The result retains:

- the active `workspace_id` and `source_ref`;
- `index_version` and current `content_sha256`;
- exact `line_start`/`line_end`;
- `quote` and its `quote_sha256`;
- a distinct `passage_evidence_id` for each line.

No full-document shortcut, new index, cache, or alternate read path is needed.
The existing opaque evidence IDs then flow unchanged through Search,
`read_verified_note`, TaskPlan assessment, claim grounding, and Reviewer. The
instruction-like line remains source text and must not be treated as a tool or
system instruction; existing grounding and model prompts continue to govern
what can be claimed.

Acceptance for this P1 slice is that the focused test sees both relation
quotes from `o-hostile.md`, each with a distinct identity and complete source
metadata, while the frozen O C baseline reaches the existing grounded Final
path. The original frozen public/Gold files and historical 12/16 result remain
unchanged.

The development O comparison below was captured at the intermediate retriever
fix, before removing an experimental query-prefix preference and applying the
assembler fix. It is retained as diagnostic evidence, not presented as a
final-version A/B measurement:

| Metric | Frozen baseline | Fixed runtime |
| --- | ---: | ---: |
| line-passage candidates for the leader query | 1 materialized | 3 materialized |
| Search / VerifiedRead calls | 2 / 2 | 2 / 2 |
| verified relation hops | 1 | 2 |
| grounded facts | `sana wu` | `sana wu`, `theo gray` |
| context bytes | 884 | 1,751 |
| outcome | `partial` / `evidence_insufficient` | `complete` |

That intermediate run kept `citation_valid=true`, `bindings_verified=true`, and
`verified_read_dependency=true`; the two reads use distinct passage evidence
IDs for lines 2 and 3. The focused coverage test passes all four checks,
including foreign-workspace filtering, caller `top_k` bounds under same-file
noise, and content-update invalidation of old evidence identities.

## Final closure

After both loss points were fixed, the exposed canary passed 2/2 offline,
including the same-file document with a leading introduction. The final
frozen twelve-case exposed regression has valid citation/read association
for 12/12; both same-file cases are complete. The separate real canary
returned `Maya Qin`, with verified facts `Lio Hart` and `Maya Qin`, 2 searches,
3 distinct verified reads, 6 model requests, and Reviewer approval. Seven
candidates were returned cumulatively across two searches; this includes
repeated candidates between searches. It consumed 43,531 input, 941 output
and 4,147 thinking tokens; the largest serialized request was 37,917 bytes.
There is no matched baseline real run for this canary, so its token delta is
N/A. The formal same-file baseline failed; its after trial was not sent
because the batch reached the two-uncertain stop rule. These different
questions cannot establish a controlled before/after success-rate improvement.

## Initial environment note

The all-case frozen runner could not initialize its HTTP-backed decision fixture
in this Windows sandbox (`WinError 10013` while binding the local test port).
The relation C path was executed directly and produced the reproduction above;
no Provider or real-vault access occurred.
