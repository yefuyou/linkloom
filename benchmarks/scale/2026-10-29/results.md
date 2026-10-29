# Offline scale benchmark

Commit: f9e8f73ca Merge pull request #27 from yefuyou/codex/linkloom-open-source-launch; branch: scale-bench.
CPU: AMD Ryzen 7 5800H with Radeon Graphics (8 cores/16 threads); RAM: 13.86 GiB; Windows-11-10.0.26300-SP0; Python 3.13.5.
Available RAM at start: 2.19 GiB; measurements use the current desktop load.
Embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 on CPU, existing local cache only.

| Component | Scale | Build s | Peak RSS MiB (build delta) | p50 ms | p95 ms | Recall@5 / candidate recall@5 | Wrong bindings |
|---|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 1,000 | 0.350 | 71.8 (+28.5) | 6.315 | 7.552 | 100.00% | — |
| hybrid | 1,000 | 42.207 | 890.3 (+847.2) | 47.005 | 55.438 | 83.00% | — |
| bm25 | 10,000 | 4.112 | 337.5 (+284.0) | 79.801 | 102.364 | 100.00% | — |
| hybrid | 10,000 | 312.212 | 1187.9 (+1134.2) | 242.353 | 542.797 | 94.00% | — |
| bm25 | 100,000 | 49.740 | 2942.0 (+2782.8) | 958.295 | 2278.611 | 100.00% | — |
| hybrid | 100,000 | skipped | — | — | — | — | — |
| temporal/get_as_of | 100,000 | — | — | 0.731 | 0.980 | — | — |
| temporal/lookup | 100,000 | — | — | 0.851 | 1.185 | — | — |
| routing | 4 | — | — | 0.239 | 0.284 | 80.00% | 0 |
| routing | 100 | — | — | 5.272 | 6.327 | 61.00% | 0 |
| routing | 1,000 | — | — | 245.420 | 330.947 | 60.50% | 0 |

## Measurement definitions

- Existing BM25Index, VectorIndex, HybridRetriever, TemporalDecisionStore and discover_scope are called unchanged. No LLM or remote provider.
- Python network connections are denied; Hugging Face/Transformers offline flags are set; local embeddings run on CPU. No generated corpus is a real vault.
- Each measured component/scale runs in a fresh process, sequentially. Corpus generation and imports of retrieval components precede the RSS baseline. Hybrid build includes first model import/load, BM25 build, embeddings and normalization; model load and embedding time are separate JSON fields.
- Build peak RSS is total process RSS sampled every 10 ms; parentheses show peak minus post-corpus baseline, including the local embedding runtime/model for hybrid. Query peak/resident RSS are also saved in JSON. These are resident bytes, not Python-only allocations.
- Five warm-up queries; 100 distinct standard retrieval queries (50 current/50 historical), one gold document each, same questions at every scale; p50/p95 use linear interpolation. Query latency includes query embedding and fusion. Build timings are one run, not repeated-run distributions.
- Every 10-document group has 1 approved current, 1 approved superseded, 6 unapproved near duplicates and 2 same-name records in other projects. Corpus hashes, answer keys, retrieved IDs and per-query measurements are saved in JSON.
- Temporal: 100,000 decisions, 1,000 slots x 100 versions, on-disk SQLite with existing indexes. Bulk synthetic seeding is untimed; 300 warm-cache API queries include historical/current/boundary/prehistory cases and evidence reconstruction. EXPLAIN uses captured SQL from those exact APIs, without INDEXED BY or added indexes.
- Routing: reviewed, verified synthetic slot inventory passed directly to model-free discover_scope. Candidate recall@5 is macro recall against all eligible slots for positive/ambiguous queries; unknown queries are excluded. Unique-target recall is separate. Wrong binding means any returned scope for an ambiguous/unknown case, or a scope outside gold. No LLM binding stage is simulated.
- Hybrid 100k is skipped if 10k measured document embedding time x10 exceeds --hybrid-100k-embedding-limit-s (default 300 seconds); this is an embedding-time projection, not a measured 100k result.

Skipped hybrid 100000: 10k document embeddings took 289.01s; linear 100k projection 2890.15s exceeds configured 300.00s limit.

### temporal/get_as_of: EXPLAIN QUERY PLAN

~~~sql
SELECT 1 FROM decision WHERE workspace_id = 'synthetic-scale' AND subject_key = 'slot-000782' LIMIT 1
~~~
- SEARCH decision USING COVERING INDEX idx_decision_workspace_subject (workspace_id=? AND subject_key=?)

~~~sql
SELECT * FROM decision
            WHERE workspace_id = 'synthetic-scale' AND subject_key = 'slot-000782'
              AND valid_from <= '2025-02-19T00:00:00.000000+00:00'
              AND (valid_to IS NULL OR valid_to > '2025-02-19T00:00:00.000000+00:00')
              AND memory_state IN ('ACTIVE', 'SUPERSEDED')
            ORDER BY valid_from DESC, decision_id
            LIMIT 1
~~~
- SEARCH decision USING INDEX idx_decision_workspace_subject_validity (workspace_id=? AND subject_key=? AND valid_from<?)
- USE TEMP B-TREE FOR RIGHT PART OF ORDER BY

~~~sql
SELECT evidence.evidence_ref
            FROM decision_evidence AS evidence
            JOIN decision AS owner
              ON owner.decision_id = evidence.decision_id
            WHERE owner.workspace_id = 'synthetic-scale'
              AND evidence.decision_id = 'decision-078249'
            ORDER BY evidence.ordinal, evidence.evidence_ref
~~~
- SEARCH owner USING INDEX sqlite_autoindex_decision_1 (decision_id=?)
- SEARCH evidence USING INDEX sqlite_autoindex_decision_evidence_1 (decision_id=?)
- USE TEMP B-TREE FOR ORDER BY

### temporal/lookup: EXPLAIN QUERY PLAN

~~~sql
SELECT 1 FROM decision WHERE workspace_id = 'synthetic-scale' AND subject_normalized = 'project-000782 atlas' LIMIT 1
~~~
- SEARCH decision USING COVERING INDEX idx_decision_workspace_subject_relation (workspace_id=? AND subject_normalized=?)

~~~sql
SELECT 1 FROM decision WHERE workspace_id = 'synthetic-scale' AND subject_normalized = 'project-000782 atlas' AND relation_normalized = 'uses supplier' LIMIT 1
~~~
- SEARCH decision USING COVERING INDEX idx_decision_workspace_subject_relation (workspace_id=? AND subject_normalized=? AND relation_normalized=?)

~~~sql
SELECT * FROM decision
            WHERE workspace_id = 'synthetic-scale'
              AND subject_normalized = 'project-000782 atlas'
              AND relation_normalized = 'uses supplier'
              AND valid_from <= '2025-02-19T00:00:00.000000+00:00' AND (valid_to IS NULL OR valid_to > '2025-02-19T00:00:00.000000+00:00') AND memory_state IN ('ACTIVE', 'SUPERSEDED')
            ORDER BY valid_from DESC, decision_id
~~~
- SEARCH decision USING INDEX idx_decision_workspace_subject_relation (workspace_id=? AND subject_normalized=? AND relation_normalized=? AND valid_from<?)
- USE TEMP B-TREE FOR RIGHT PART OF ORDER BY

~~~sql
SELECT evidence.evidence_ref
            FROM decision_evidence AS evidence
            JOIN decision AS owner
              ON owner.decision_id = evidence.decision_id
            WHERE owner.workspace_id = 'synthetic-scale'
              AND evidence.decision_id = 'decision-078249'
            ORDER BY evidence.ordinal, evidence.evidence_ref
~~~
- SEARCH owner USING INDEX sqlite_autoindex_decision_1 (decision_id=?)
- SEARCH evidence USING INDEX sqlite_autoindex_decision_evidence_1 (decision_id=?)
- USE TEMP B-TREE FOR ORDER BY

Routing 4: unique-target candidate recall@5 100.00%; 24 queries, 16 require abstention.

Routing 100: unique-target candidate recall@5 100.00%; 120 queries, 80 require abstention.

Routing 1000: unique-target candidate recall@5 100.00%; 120 queries, 80 require abstention.

## Observed bottlenecks

The first measured hybrid scale (1,000) already takes 42.21s to build, including 17.93s model load and 23.34s document embedding. At 10,000, embedding alone takes 289.01s out of 312.21s build time. These timings identify embedding/model startup as the earliest build cost.

BM25 at 100,000: build 49.74s, query p95 2278.61ms, build peak RSS 2942.0 MiB. The existing code constructs scope indexes and scores/sorts document candidates; no implementation was changed.

Routing at 1,000 slots: p95 330.95ms; unique-target recall 100.00%; wrong bindings 0. Cross-project candidate recall@5 is 2.50%. Same-name-only questions return no candidates in this fixture; conflicting project scopes expose a broad ambiguous candidate set truncated to five, reducing candidate recall while abstaining from binding.

## Reproduce

~~~powershell
python -B scripts/bench_scale.py
~~~

## 局限说明

合成数据的标准答案按字面植入，结果偏向字面匹配，不能用来比较 BM25 和嵌入检索的语义能力。
