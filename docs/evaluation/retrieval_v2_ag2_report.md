# Retrieval V2 Benchmark Execution & Analysis Report (AG-2)

- **Worker**: AG-2 (High-throughput Retrieval Benchmark Worker)
- **Target Repository**: LinkLoom
- **Execution Date**: 2026-09-23
- **Primary Artifact**: `docs/evaluation/retrieval_v2_ag2_results.json`
- **Reference Baseline**: `docs/evaluation/retrieval_v2_benchmark.json` (Codex)

---

## 1. Execution Commands & Environment

The benchmark was executed in fully offline, local-model mode using the frozen runner and seed fixtures:

```powershell
$env:PYTHONPATH='src'; $env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; python scripts/run_retrieval_v2_benchmark.py --output docs/evaluation/retrieval_v2_ag2_results.json
```

- **Environment**: Local PyTorch CPU (`2.9.0+cpu`), sentence-transformers offline cache
- **Embedding Model**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- **Network / Paid Providers**: None (100% offline, zero network egress)

---

## 2. Dataset Integrity & Cryptographic Hashes

Integrity verification of `docs/requirements/m1_team_decision_eval_seed/dataset.jsonl`:

| Check Point | SHA-256 Checksum | State |
| :--- | :--- | :--- |
| **Pre-execution** | `2b150f3ba79f991aaa72fecfd7bffe7d83e6f3fb3656e453d1c2dfadc879b983` | Baseline |
| **Post-execution** | `2b150f3ba79f991aaa72fecfd7bffe7d83e6f3fb3656e453d1c2dfadc879b983` | **Unchanged (Identical)** |
| **Reference Codex** | `2b150f3ba79f991aaa72fecfd7bffe7d83e6f3fb3656e453d1c2dfadc879b983` | **Matches Perfectly** |

Zero mutation occurred to the frozen Gold dataset, source notes, or workspace markdown documents.

---

## 3. Aggregate Performance Metrics Table

All 30 evaluation cases are present across all 5 evaluation modes.

| Evaluation Mode | Recall@1 | Recall@3 | Recall@5 | MRR | NDCG@5 | Failure Rate | Availability@5 | Mean Latency (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Current** | 0.3105 | 0.6750 | 0.9283 | 0.8278 | 0.8245 | 0.0000 | 1.0000 | 3.9559 |
| **BM25** | 0.3300 | 0.7233 | 0.9239 | 0.8694 | 0.8537 | 0.0000 | 1.0000 | 0.6118 |
| **Dense** | 0.3967 | 0.7039 | 0.9378 | 0.9500 | 0.8851 | 0.0000 | 1.0000 | 32.0448 |
| **Hybrid** | 0.3633 | 0.7456 | 0.9361 | 0.9333 | 0.8895 | 0.0000 | 1.0000 | 31.8911 |
| **Directory-aware Hybrid** | 0.3633 | 0.7456 | 0.9361 | 0.9333 | 0.8895 | 0.0000 | 1.0000 | 32.3351 |

### Comparison Against Reference Codex Baseline
- **Retrieval Metrics**: 100% exact numerical agreement across all 7 retrieval metrics (`diff = 0.0000`).
- **Latency Variance**: Minor hardware fluctuations on local CPU:
  - BM25: 0.61 ms vs 0.48 ms (+0.13 ms)
  - Current: 3.96 ms vs 4.18 ms (-0.22 ms)
  - Dense: 32.04 ms vs 30.52 ms (+1.52 ms)
  - Hybrid: 31.89 ms vs 31.19 ms (+0.70 ms)
  - Directory Hybrid: 32.34 ms vs 31.88 ms (+0.46 ms)

---

## 4. Per-Case Channel Comparisons

Each channel was evaluated against the frozen evaluator-side `relevant_source_refs` (30 cases total):

### 4.1 Lexical Wins (BM25 > Dense) — 13 Cases
Cases where exact lexical matching outperformed vector representations:
* `mps-001`, `mps-002`, `mps-003`, `mps-004`
* `ret-003`, `ret-005`
* `inc-002`, `inc-003`, `inc-005`
* `aer-002`, `aer-005`
* `iti-003`
* `drm-003`

**Key Pattern**: These cases contain specific entity names (`Atlas Lantern`, `Borealis B`, `Cedar C`), incident codes, explicit dates, or exact technical keywords. BM25 directly scores exact token matches in markdown titles and section headings, whereas Dense vectors disperse similarity mass over broader semantic themes.

### 4.2 Dense Wins (Dense > BM25) — 13 Cases
Cases where dense semantic embeddings outperformed lexical matching:
* `mps-005`
* `ret-002`, `ret-004`
* `inc-001`, `inc-004`
* `aer-003`, `aer-004`
* `iti-001`, `iti-002`, `iti-005`
* `drm-002`, `drm-004`, `drm-005`

**Key Pattern**: These cases involve conceptual/paraphrased user inquiries without verbatim note titles (e.g. `confirmed root cause`, `incident-remediation actions remain open`, `why office-hours prototype does not prove adoption`). Dense vectors bridge the vocabulary gap where lexical term frequency under-rewards conceptual matches.

### 4.3 Ties (BM25 == Dense) — 4 Cases
Cases where both channels scored identical metrics:
* `ret-001`, `aer-001`, `iti-004`, `drm-001`

**Key Pattern**: Direct, canonical decision questions with both strong title keywords and clear semantic intent; both retrievers attained NDCG@5 = 1.0000, Recall@5 = 1.0000, and MRR = 1.0000.

---

## 5. Directory Scope Impact & Failure Analysis

### 5.1 Directory-Scope Impact
* **Observed Difference**: Exactly **0** differences in ranked documents and metrics between `hybrid` and `directory_hybrid`.
* **Root Cause Analysis**: In the frozen benchmark workspace fixtures (`docs/requirements/m1_team_decision_eval_seed/workspaces/`), all markdown documents within any workspace reside in a single flat directory (`/workspaces/<workspace_id>/`). No subdirectories exist. Consequently, directory-level token matching resolves to the single containing directory, producing an identical candidate pool as unconstrained hybrid retrieval.

### 5.2 Failure & Imperfect Recall Cases
* **Hard Failures (`retrieval_failure == 1.0`)**: **0 across all modes** (`retrieval_failure_rate = 0.0000`). At least one gold evidence note was ranked within top-5 for every single query (`evidence_availability_at_5 = 1.0000`).
* **Partial Recall (`recall_at_5 < 1.0`)**:
  - BM25: 8 cases (`mps-002`, `mps-005`, `ret-002`, `aer-003`, `iti-002`, `iti-005`, `drm-004`, `drm-005`)
  - Dense: 7 cases (`mps-002`, `ret-003`, `ret-005`, `inc-002`, `aer-002`, `aer-003`, `drm-005`)
  - Hybrid: 7 cases (`mps-002`, `mps-005`, `ret-002`, `ret-003`, `aer-002`, `aer-003`, `drm-002`)
* **Important Case Study (`drm-002`)**:
  - Gold relevant notes: 4 notes (`02-retention-review.md`, `03-retention-decision.md`, `04-legal-status.md`, `05-migration-status.md`).
  - In BM25, distractor `06-compliance-office-hours.md` ranked #1, but all 4 relevant notes occupied ranks #2-#5 (Recall@5 = 1.0).
  - In Dense, distractor `01-90-day-draft.md` ranked #2, but all 4 relevant notes occupied ranks #1, #3, #4, #5 (Recall@5 = 1.0).
  - In Hybrid (RRF fusion, $k=60$), both high-ranking distractors (`06-compliance-office-hours` and `01-90-day-draft`) received enough channel boost to occupy slots in top-4. This pushed `02-retention-review.md` out to rank #6, reducing Hybrid's Recall@5 to 0.75. This demonstrates the known RRF behavior where non-overlapping false positives from distinct channels can displace marginal relevant items in fixed top-k windows.

---

## 6. Benchmark Limitations

1. **Flat Directory Topology**: The current synthetic seed workspaces feature flat note hierarchies, meaning directory-aware routing cannot be evaluated for deep hierarchical vault navigation.
2. **Top-K Cutoff vs. Multi-Note Synthesis Ground Truth**: Certain complex synthesis questions require 4 to 5 relevant notes. When top_k is capped at 5, even a single distractor ranking in the top 5 artificially caps maximum recall.
3. **Corpus Size**: The 30-case evaluation suite operates over 36 synthetic notes (6 workspaces $\times$ 6 notes). While statistically robust for regression checks, enterprise-scale latency profiling (thousands of notes) will require expanded corpus scaling.
