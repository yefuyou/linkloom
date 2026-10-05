# Retrieval V2 Downstream Comparison

Frozen cases: 30 across 6 workspaces and 36 notes. Gold unchanged: **True**.

| Mode | Evidence availability @5 | TeamDecision contract pass | Grounding pass | Mean evidence context (bytes) | Mean retrieval latency (ms) | Semantic result |
|---|---:|---:|---:|---:|---:|---|
| current | 1.000 | 1.000 | 1.000 | 3312.0 | 2.39 | N/E |
| hybrid | 1.000 | 1.000 | 1.000 | 3360.2 | 609.20 | N/E |

## Interpretation

Contract and grounding columns are deterministic boundary probes using a synthetic non-business result. They show that returned source evidence can pass the existing strict contract and observed-evidence check; they do not measure answer quality.

Semantic result is **N/E**: this offline run invokes no real model/provider, so it does not establish semantic accuracy or a real-model improvement. Context size is the UTF-8 byte size of serialized retrieved evidence results, not provider token usage. Latency includes cold index initialization on the first query for each workspace; per-case details and hashes are in the JSON artifact.

Gold SHA-256: `2b150f3ba79f991aaa72fecfd7bffe7d83e6f3fb3656e453d1c2dfadc879b983`.
