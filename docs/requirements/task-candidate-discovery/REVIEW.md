# Independent acceptance review — task candidate discovery

Date: 2026-10-18. Reviewer: independent `candidate_review` sub-agent
(`gpt-5.6-luna`, max reasoning), read-only review. Worker did not self-approve.
The reviewer inspected the dedicated SPEC, implementation, tests, synthetic
evaluation and delivery artifacts. No P4 file, Provider or old heldout was used.

## Initial findings and resolution

1. **Required — bound intermediate candidate construction.** Output truncation
   originally followed complete candidate construction. Added a separate
   `max_generated_candidates` budget (default 1000, ceiling 5000), checked before
   construction, evidence hashing or merging. All pair/change signals share it.
   Exceeding it raises ValueError without returning partial results. Per-task
   keys, namespace, tokens, parsed sentences, source aliases, references and raw
   quotes are precomputed. Tests cover pair and change budget exhaustion.
2. **Required — observable budgets and fanout.** Added input/source-character/
   context-signal counts, generated attempts, distinct considered candidates,
   actual endpoint fanout, maximum fanout and total/per-endpoint truncation
   reason codes. Generated attempts include duplicate merge proposals; considered
   counts the deduplicated pool, returned counts final output.
3. **Optional — ambiguous multi-key observation.** Added
   `AMBIGUOUS_TASK_KEY` uncertainty and a contract test. It remains a lead, never
   a supported identity assertion.

## Independent re-review

Reviewer conclusion: both Required findings and the multi-key Optional are
resolved; **no Critical or Required blockers remain**.

The reviewer independently ran the dedicated suite after repair:

```text
30 passed in 1.42s
```

The reviewer also confirmed that a 64-observation synthetic snapshot now fails
explicitly at the generation budget, rather than constructing the entire pool.
Its one local timing probe is diagnostic evidence, not a latency benchmark.

The worker separately ran the relevant 185 M1/P1/P2/P3/source-identity tests;
the reviewer did not rerun or take authorship of that evidence. The 12-query
synthetic comparison remains Recall@5 8/9 versus 1/9, with the documented C10
omission and project-only noise. These are accepted scope limitations, not
hidden passing cases or confirmed business relationships.

RELATED_TO's symmetric candidate deduplication versus the existing P2 directional
proof check is explicitly documented as a P4 integration contract. The existing
P2 whitespace limitation in the C02 integration probe is also recorded; no
public validator interface was modified.

Acceptance applies to the bounded offline module, tests and evidence. It does
not establish P4 integration, CI, deployment, real-Provider or end-to-end Agent
success. No merge or M3 work is authorized by this review.
