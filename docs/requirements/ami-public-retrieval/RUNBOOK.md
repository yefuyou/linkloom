# Reproduce the AMI official-link component benchmark

Run in this checkout with Python >=3.11 and the existing project dependencies
(`.[dev]`; optional local retrieval dependencies `.[retrieval]`). No API key is
needed. Obtain the official archive from the URL in
`evals/ami_public_v1/ATTRIBUTION.md`; the preparation command verifies its hash.
No source media are needed. Keep the archive outside prediction inputs.

Use fresh output directories each time; commands reject existing output files.
Do not run or modify the historical reference scripts or seals.

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
python -m pytest -q --basetemp=work/pytest-ami-fresh tests/unit/test_ami_nxt.py tests/unit/test_ami_public_evaluation.py
python -m linkloom.evaluation.ami_public.prepare --archive work/upstream/ami_public_manual_1.6.2.zip --reference-root evals/ami_public_v1/frozen --output work/ami-fresh/data
python -m linkloom.evaluation.ami_public.predict --inputs work/ami-fresh/data/inputs --output work/ami-fresh/run
python -m linkloom.evaluation.ami_public.score --data work/ami-fresh/data --run work/ami-fresh/run --reference-run evals/ami_public_v1/frozen/reference_run --output work/ami-fresh/score
```

Preparation is a custodian operation and reads the archive plus frozen qrels.
Prediction is a separate process and accepts only a prepared input root. Its
strict schemas and audit hook permit only `queries.jsonl`, `passages.jsonl`,
and `manifest.json` from that root, block Gold/raw paths and network connects,
and seal prediction and manifest bytes before scoring. This process-level
boundary is tested, but is not an OS isolation guarantee against malicious
native code. Dense uses the already cached default multilingual MiniLM model;
missing cache/dependencies are `NOT_RUN`, with no network fallback.

The retrieval corpus has all 6,981 native DAs, 440 with no text. Another 17
source words lack DA references: they remain in the complete canonical source,
reverse map, and F-stage batch chunks, outside this reference-matched DA task.
Native word text is unchanged; DA text uses a declared single-space join.
Canonical headers preserve meeting/speaker/DA IDs and relative seconds. No
meeting-relative second is converted to a made-up absolute timestamp.

The scorer reports query-macro official-link Recall/Hit@1/5/10 and MRR@10.
Required Evidence Coverage is exact native DA text retained in final rendered
context, reported both as query-macro and target-pair-micro coverage. An
oversized DA requires all its mechanical pieces, not just a matching ID.
Selection losses come from the real assembler's drop reasons; post-selection
rendering losses are counted separately. A given meeting ID scopes each query;
correct-meeting hits are not an independent meeting-discovery score.

Keep `scores.json`, `bad_cases.json`, all prediction/context traces, manifests,
five N/E cases, and failed run directories. Do not tune using diagnostic errors
and then call the resulting run a first blind test. This cohort has already
been exposed and remains diagnostic.

The next F-stage slice can wrap deterministic batch text as existing ordinary
document `RawArtifact`s and reuse `parse_source_text` / discovery contracts.
Keep relative time/speaker/native mappings in the sidecar, no `event_time`
fallback. Apply existing three-call discovery batches and guards, with separate
model authorization. Public links can score limited association reproduction;
owner/deadline, cross-meeting relations, citation entailment, SemanticAnalysis
and Product E2E require additional evaluation contracts and remain unevaluated.
