# L5-1 rerank development comparison

Run: 2026-11-02T00:29:19.985105+08:00 · commit: `9233c63abb79011c4fbc5acc1094b14bce7e36ef`

Groups: A=BM25; B=existing hybrid RRF (k=60); C=B + base ONNX INT8; D=B + M3 CPU FP32; E=B + optional Jev.

Synthetic development data only. Retrieval30 is explicitly used as development data. No held-out acceptance set was evaluated.
One formal pass; A/B retrieve once and C/D/E reuse the exact RRF top-20 per query. No labels or answers enter model text.
MRR@20 uses the first relevant source in the retained 20; Recall@5 and binary-gain nDCG@5 use the first five. Macro averages; linear latency percentiles.
Rerank latency includes text/token preparation, IPC and scoring; offline preparation and synthetic warmup are excluded. A/B rerank time is zero.

| Dataset | Group | Recall@5 | MRR@20 | nDCG@5 | rerank p50 ms | p95 ms | Invalid |
|---|---|---:|---:|---:|---:|---:|---:|
| scale-1000 | A | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0 |
| scale-10000 | A | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0 |
| retrieval30-dev | A | 0.9239 | 0.8694 | 0.8537 | 0.0000 | 0.0000 | 0 |
| scale-1000 | B | 0.8300 | 0.7169 | 0.7282 | 0.0000 | 0.0000 | 0 |
| scale-10000 | B | 0.9400 | 0.6344 | 0.7039 | 0.0000 | 0.0000 | 0 |
| retrieval30-dev | B | 0.9361 | 0.9333 | 0.8895 | 0.0000 | 0.0000 | 0 |
| scale-1000 | C | 1.0000 | 0.8925 | 0.9200 | 1750.3197 | 1994.6020 | 0 |
| scale-10000 | C | 1.0000 | 0.8792 | 0.9103 | 1912.0801 | 2263.4902 | 0 |
| retrieval30-dev | C | 0.9322 | 1.0000 | 0.9335 | 1353.0898 | 1507.6306 | 0 |
| scale-1000 | D | 1.0000 | 0.9800 | 0.9852 | 12676.1747 | 24175.7157 | 0 |
| scale-10000 | D | 1.0000 | 0.9850 | 0.9889 | 12205.0401 | 13795.0292 | 0 |
| retrieval30-dev | D | 0.9433 | 0.9667 | 0.9170 | 8102.0827 | 8937.9388 | 0 |
| — | E: not_run_no_key | — | — | — | — | — | — |

## Local preparation and Jev accounting

- C: `{"model": "BAAI/bge-reranker-base", "revision": "2cfc18c9415c912f9d8155881c133215df768a70", "device": "cpu", "engine": "onnx", "first_load_ms": 19372.3389999941, "attempts": []}`
- D: `{"model": "BAAI/bge-reranker-v2-m3", "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "device": "cpu", "engine": "torch", "first_load_ms": 31179.191399976844, "attempts": []}`
- E: 未运行（本机未配置 Jev key）; `{"request_count": 0, "input_tokens": 0, "cost_usd": 0.0, "upper_bound_cost_usd": 0.0, "budget_usd": 1.0}`

INT8 settings: QInt8, per_channel=true, reduce_range=true (7-bit weight range in INT8 storage), MatMul/Gather, MatMulConstBOnly=true, opset 17. CPU threads=8, batch=8, pair limit=256 tokens.
The local base p95 target is 3000 ms for at most 20 candidates; per-dataset measured values above establish whether it was met.

## Every B < A development case

| Dataset / case | Declines | C restored | D restored | E restored |
|---|---|---|---|---|
| scale-1000 / q-000 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-002 | mrr_at_20,ndcg_at_5 | yes | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| scale-1000 / q-005 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-1000 / q-006 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-013 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-015 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-017 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-018 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-021 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-023 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-027 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-028 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-032 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-034 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-038 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-1000 / q-040 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-042 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-044 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-047 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-049 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-1000 / q-050 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-054 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-056 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-058 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-059 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-062 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| scale-1000 / q-066 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-068 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-070 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-072 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-075 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-077 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-1000 / q-084 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-086 | mrr_at_20,ndcg_at_5 | yes | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| scale-1000 / q-089 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-091 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-092 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-1000 / q-096 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-1000 / q-098 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-000 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-003 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-005 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-006 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-008 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-009 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-010 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-012 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-013 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-015 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-016 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-017 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-018 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-019 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-021 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-023 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-024 | mrr_at_20,ndcg_at_5 | yes | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| scale-10000 / q-026 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-027 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-028 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-032 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-035 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-036 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-037 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-038 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-040 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-042 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-044 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-047 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-049 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-050 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-051 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-053 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-056 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-058 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-059 | recall_at_5,mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-062 | mrr_at_20,ndcg_at_5 | yes | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| scale-10000 / q-064 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-066 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-068 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-070 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-072 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-075 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-077 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-079 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-080 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-082 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-084 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-086 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-089 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-090 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-091 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-092 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-093 | recall_at_5,mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| scale-10000 / q-097 | mrr_at_20,ndcg_at_5 | no: mrr_at_20,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| scale-10000 / q-098 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| retrieval30-dev / mps-001 | mrr_at_20,ndcg_at_5 | yes | yes | not measured |
| retrieval30-dev / mps-003 | ndcg_at_5 | yes | no: ndcg_at_5; ranking_error_or_window_truncation_possible | not measured |
| retrieval30-dev / ret-003 | recall_at_5,ndcg_at_5 | no: recall_at_5,ndcg_at_5; ranking_error_with_relevant_sources_available | yes | not measured |
| retrieval30-dev / ret-005 | ndcg_at_5 | no: ndcg_at_5; ranking_error_with_relevant_sources_available | no: ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| retrieval30-dev / inc-002 | ndcg_at_5 | no: ndcg_at_5; ranking_error_with_relevant_sources_available | no: ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| retrieval30-dev / aer-002 | recall_at_5,ndcg_at_5 | yes | yes | not measured |
| retrieval30-dev / aer-003 | ndcg_at_5 | yes | no: ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| retrieval30-dev / iti-003 | ndcg_at_5 | no: ndcg_at_5; ranking_error_with_relevant_sources_available | no: ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| retrieval30-dev / drm-002 | recall_at_5,ndcg_at_5 | no: recall_at_5; ranking_error_with_relevant_sources_available | no: recall_at_5,ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |
| retrieval30-dev / drm-003 | ndcg_at_5 | no: ndcg_at_5; ranking_error_with_relevant_sources_available | no: ndcg_at_5; ranking_error_with_relevant_sources_available | not measured |

Each row, including drm-002, has its A/B/C/D/E metrics, source ranks, recall ceiling and remaining failure reasons in results.json. Score scales are never compared across BM25, RRF and rerankers.
Window omission vs semantic confusion is marked as a hypothesis. Ranking agreement or development gains are not held-out task accuracy.

## Failures and regressions relative to B

### C
- scale-1000/q-007: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-010: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-030: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-036: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-039: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-043: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-045: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-051: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-057: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-061: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-065: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-082: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-1000/q-088: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-030: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-039: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-043: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-045: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-046: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-052: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-054: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-057: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-070: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-083: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-087: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-088: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/ret-002: Recall@5=0.7500; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/ret-003: Recall@5=0.6667; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/ret-005: Recall@5=0.7500; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/inc-002: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/inc-004: Recall@5=0.5000; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/inc-005: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/aer-003: Recall@5=0.7500; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/iti-002: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/iti-003: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/drm-002: Recall@5=0.7500; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/drm-003: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/drm-005: Recall@5=0.8000; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
### D
- scale-1000/q-087: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- scale-10000/q-002: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/mps-003: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering/window omission are possible causes.
- retrieval30-dev/ret-002: Recall@5=0.7500; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/ret-005: Recall@5=0.7500; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/inc-001: Recall@5=1.0000; below B: mrr_at_20,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/inc-004: Recall@5=0.5000; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/aer-003: Recall@5=0.7500; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/iti-003: Recall@5=1.0000; below B: ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/drm-002: Recall@5=0.7500; below B: none; relevant sources present; reranker ordering fails to prioritize all gold evidence.
- retrieval30-dev/drm-005: Recall@5=0.8000; below B: recall_at_5,ndcg_at_5; relevant sources present; reranker ordering fails to prioritize all gold evidence.
### E

[Raw measurements and model/data manifests](results.json)


## 中文解读与运行限制

本次仅为一次开发集对比。scale 两档使用同一批 100 个问题、不同规模的嵌套文档库；冻结 30 题也已按本任务约定用作开发集。没有新增保留集成绩。

测试期间机器并非独占：00:46 的只读 2 秒采样显示，评测索引进程约占用 7.26 个 CPU 核，其他 Python 任务合计约占用 1.76 个核；当时可用内存约 2541 MiB。各组/数据档的开始前可用内存以 results.json 的 before/before_prepare 为准。延迟仅代表本次机器状态，不能当成空载机器基线。

### drm-002 的已确认问题

该题同时要求解释保留策略的选择原因和迁移已完成/受阻检查点，相关来源共 4 篇。BM25 的 Recall@5=1、MRR@20=0.5、nDCG@5=0.7606；RRF 变为 0.75、1、0.7366。这是不同排名指标之间的取舍，不能比较 BM25 原分数和 RRF 数值来判断质量。

base INT8 的 Recall@5 仍为 0.75，MRR@20=1、nDCG@5=0.8319。漏掉的相关文档 03-retention-decision.md 仍在全部 6 条候选中，却被排到第 6；第 4/5 为非相关的讨论纪要和已废弃草稿。该题所有片段均未截断，故可以确认是重排次序错误，无法归因于 RRF20 召回缺失或 256-token 截断。模型没有把“正式决策”和多来源解释需求同时排进前 5；关于模型内部偏好的解释只能作为假设。

C 使用 base INT8，D 使用不同的 M3 模型；本实验没有 base FP32 对照组，因此 C/D 差异不能单独归因于量化。

### base 的剩余首位误排

scale-1000 中有 19 题、scale-10000 中有 22 题的首位结果为未审批草稿；两档正确文档均已全部进入前 5，因此 Recall@5=1，但 MRR@20 仍未达到 BM25 的 1。按照合成数据生成规则及候选正文核对，这些首位错误均为同项目、正文明确标为 Unapproved draft 的文档。两档分别涉及当前问题 8/9 题、历史问题 11/13 题。

例如 scale-1000/q-005：问题明确要求 before 2026-02-01 的 approved supplier；base 把正文以 Unapproved draft 开头的 doc-000047-7 排第 1，正确的已审批 doc-000047-1 排第 2。scale-10000/q-015 中同类草稿排第 1，正确文档排第 3。候选里包含正确信息；模型的相关性排序没有充分区分审批状态，不能替代既有权威性判断。没有据此修改检索规则、槽位路由、标注或模型参数。


### C/D 救回范围与新增退步

下表“救回”指该题 B 低于 A 的所有指标均恢复至 A；各指标的其他新增回退另列在原报告 Failures and regressions relative to B。

| 开发集 | B<A 题数 | C 救回 | D 救回 | C 相对B有任一指标下降 | D 相对B有任一指标下降 |
|---|---:|---:|---:|---:|---:|
| scale-1000 | 39 | 33 | 36 | 13 | 1 |
| scale-10000 | 56 | 45 | 54 | 12 | 1 |
| retrieval30-dev | 10 | 4 | 3 | 8 | 7 |

### drm-002 的 M3 结果

M3 的 Recall@5=0.75、MRR@20=1、nDCG@5=0.7366，同样没有恢复到 BM25 的 Recall@5=1。两种重排都不是通用的业务权威性/多来源覆盖判定器。下表为完整 6 条候选的原始次序与重排结果：

| 文档 | 相关 | A rank | B rank | C rank / logit | D rank / logit |
|---|---|---:|---:|---:|---:|
| 05-migration-status.md | 是 | 2 | 1 | 1 / 2.3052 | 1 / 1.9145 |
| 06-compliance-office-hours.md | 否 | 1 | 2 | 4 / -4.7381 | 2 / -3.5006 |
| 03-retention-decision.md | 是 | 4 | 3 | 6 / -6.8739 | 5 / -6.1446 |
| 01-90-day-draft.md | 否 | 6 | 4 | 5 / -4.9440 | 4 / -4.6031 |
| 04-legal-status.md | 是 | 3 | 5 | 3 / -4.5608 | 6 / -6.3370 |
| 02-retention-review.md | 是 | 5 | 6 | 2 / -3.6023 | 3 / -4.0134 |

C 把相关的正式决策文档排第 6；D 将该文档排第 5，却把相关的法律状态文档排第 6。两种模型均未截断；原始 RRF20 召回上限为 1，因此本例可以确认为次序错误。BM25/RRF/不同模型的分数不在同一量纲，报告只对比排名指标，不对比分数大小。

### 验收结论与性能边界

- C 三档 p95=1.995/2.263/1.508 秒，均满足本次 base 热重排 p95≤3 秒目标；D 为 24.176/13.795/8.938 秒。
- M3 在 scale 的 MRR@20 更高；冻结 30 题中，它的 Recall@5 高于 base，但 MRR@20/nDCG@5 低于 base，没有单一模型在所有指标上占优。按已批准方案保留 base INT8 为本地默认；这些开发结果不能当保留集准确率。
- 冷加载 C=19.372 秒、D=31.179 秒，使用已存在的本地模型/工件；不包含初次网络下载或导出量化时间。产品首次准备在隔离 worker 后台执行，当次检索保留 RRF。
- E 未运行：本机未配置 Jev key；实际 0 请求，0 token，费用 $0。Jev 请求格式/故障处理经过假 transport 单测，未进行真实远端验收。
- 本地全量 pytest 仅后台运行一次：4125 passed / 33 skipped，2153.28 秒。相关重排验收 97 passed；后续仅隔离测试可选依赖的改动以相关 11 项单测验证。
- 从最终 JSON 逐项校验：A/B 各230题，C/D各230题且0 invalid；C/D与B候选集合一致，原 score/来源未替换、rank连续、每对输入≤256 tokens；冻结开发集 hash 和已评测源码指纹一致。
- work/方案、环境、模型和密钥不进入产品提交；scope_discovery/question_routing 与前端没有改动。平台 CI 结果以 PR checks 为准。
