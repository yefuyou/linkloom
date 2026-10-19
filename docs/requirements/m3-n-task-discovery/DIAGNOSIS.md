# M3-N diagnosis and compatibility boundary

## Established failure layer

The original F model request is
`../m3-task-discovery/evidence/live/request-1.json`. Its single exact document
window contains all five original messages, including:

> [2029-06-01T09:01:00Z] 周岚: 索引迁移还包括补齐回归脚本。

`response-1.json` emits PLANs whose description_parts are `索引迁移` and
`提交容量报告`. It also emits FACT/PROPOSAL/DECISION claims, but no claim or task
description for `补齐回归脚本`. The recorded projection has two observations,
with no rejected task proposal. Thus this missing description was already
absent before `_project`, M1 or `task_discovery_veto.non_asserted`. There is no
evidence for a regex veto causing this omission.

Two input limitations are directly observable: the old general prompt asks for
ONE task statement and forbids merging/cross-segment field carry; the aggregate
window has speaker=null and no structured per-message speaker map. Headers and
complete message bodies are present. Lack of truncation is established; the
relative causal contribution of instruction, structure, model variability or
task-splitting behavior is **not** established without a new model ablation.

The previous F/J interface had no task-specific optional context. M3-N resolves
that interface restriction; it cannot retroactively recover model omissions.

## Reproducible Before / After

`evidence/historical-diagnosis.json` records original file hashes, exact second
message, raw claim classes, raw task parts and both normalized requests.
Passing the SAME frozen model claims through the old and new task profile
produces the SAME descriptions: `索引迁移`, `提交容量报告`. Neither invents the
omitted work. New request/config/extraction hashes differ; evidence identity,
offsets and source validation do not.

`evidence/historical-replay/` uses the historical exact request equality guard
for all three F sources. Canonical LF source/label snapshots come from original
Git blobs into new output files, preserving the Windows checkout and historical
files. `m3j-replay.replay-score.json` retains strict Recall=4/5, Precision=4/5,
matched fields=24/24, evidence=5/5. No historical replay, Gold or score is edited.

The new public dev/holdout sets are frozen in `data/freeze_manifest.json` before
production edits. Positive proposals are supplied by an oracle; negative quoted,
cancelled/conditional descriptions are deliberately cropped adversarial model
outputs. Before and After measure the same Host contracts. They are not blind
inference, and their perfect bounded scores cannot demonstrate model quality.
The independently designed holdout is independent of implementation decisions,
but public and scripted, not a provider-quality holdout.

## Minimal shared input

`TaskDiscoveryContext` accepts a bounded immutable RawArtifact and derives
canonical segments using the existing parser. It rejects incomplete parsing,
foreign workspaces, mismatched windows and unsupported context types before
request authorization. It accepts neither arbitrary prompt text nor caller-
invented speakers/offsets. F still authorizes every source before constructing
the optional context and checks it after each call and before releasing results.

The new profile supplies exact unchanged source text once and a message map of
speaker, speech time and Unicode body offsets. Task instructions explicitly
cover additions/continuation, independent work, quoted history, cancellation
versus discussion, conditional work, missing fields and mixed languages. Only
model-proposed original descriptions enter existing projection and veto. No
new extraction, task identity, operation, runtime or semantic authority exists.

Use the opt-in F service as follows:

```python
TaskObservationDiscoveryService(
    extractor, source_registry=host_registry, current_source=host_current_source,
    task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1,
)
```

Callers may alternatively supply
`SemanticExtractionContext(workspace, resolver, task_discovery=TaskDiscoveryContext(artifact))`
to the existing extractor. The task context must match its exact extraction
window; no other source text can enter by this hook.

Absent optional context, the old prompt, schema, configuration hash, request
identity, guard, retry behavior and provider options remain unchanged. Direct
tests compile the baseline extractor from Git and compare normalized requests
and fingerprints. Product/Canary callers are not switched; M3-L remains default
OFF and frozen. This is an explicit offline-ready entry point, not a deployment
of unmeasured semantic behavior.

The task prompt has profile `task-discovery-cross-message/v1` and context contract
`task-discovery-context/v1`. Both, the context hash, prompt hash and extraction
fingerprint distinguish it from historical runs. Schema stays semantic-candidate/v2.
