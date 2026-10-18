# Synthetic task candidate discovery evaluation

`inputs/workspaces.json` contains twelve new independent Workspace snapshots,
with source-bound M1 extraction proposals and a query anchor. These are public
development examples, not a heldout or representative production sample.
No old Dev/heldout evaluator or Provider is executed.

`gold/relevance.json` is evaluator-only endpoint relevance: whether the other
observation/change is worth investigating. It does not say that a relationship
is supported. Neither the production module nor the input loader reads Gold.
Predictions for both methods are written before the scorer opens that file;
input, prediction and Gold SHA-256 hashes accompany the result.
Text hashes use UTF-8/LF normalization, including the delivery integrity manifest,
so Git's Windows newline conversion does not change the reproducibility check.

Recall@K is micro endpoint recall over nine relevant endpoints. A candidate
consumes one slot, even if another predicate hint names the same pair; reports
include unique endpoint counts so this cost remains visible. Noise is returned
candidates whose endpoint is outside the relevance set, including empty queries.
K is 1, 3 and 5 per query, with matching per-observation fanout/output budgets.

The baseline uses normalized exact description equality within the workspace.
It has no task-key, scope, alias, source-reference or supplier-change features.
Both methods receive precisely the same verified synthetic observations and
sources. This deliberately simple baseline establishes a reproducible comparison,
not a claim of superiority to BM25, embeddings or an optimized retriever.

`inputs/original-five-lines.txt` is the original public five-line example copied
into a separate safety workspace. It is a negative safety probe outside recall
scoring, not a rerun of an old benchmark. It must not establish a revision or
compute a four-day delay. Additional probes update task, alias and change sources
and revalidate the old candidates.

Reproduce from the task worktree, without installation or network:

```powershell
$env:PYTHONPATH='src'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/unit/test_task_relation_candidates.py tests/unit/test_task_candidate_evaluation.py -q -p scripts.evaluation_offline_plugin -o cache_dir=work/pytest-cache
python scripts/evaluate_task_candidates.py --output work/discovery-eval-new
```

Use a fresh output directory; existing output is preserved. Generated artifacts
remain under this worktree's ignored `work/` directory. The delivery evidence
contains selected sanitized deterministic JSON copies.
