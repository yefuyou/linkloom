# M3-N delivery

Implemented and locally verified an **optional** task-specific context/profile
in the existing shared extractor. It is ready for offline use; real-model
semantic quality and semantic-recall improvement remain **NOT_EVALUATED**.
The product default and frozen LIVE Canary continue using the historical profile.

## Failure and change

The original five-message request includes the complete supplementary message.
The model response omits its work description before projection/veto. The old
single-statement instruction and null aggregate speaker are observed input
limitations, not a proven causal model ablation. See [diagnosis](DIAGNOSIS.md).

Production changes:

- `src/linkloom/semantic_ingestion/extraction.py`: immutable typed
  `TaskDiscoveryContext`, canonical message map, optional context on the shared
  interface, task-only prompt with independent version/config/context fingerprint.
- `src/linkloom/semantic_ingestion/task_observation_discovery.py`: explicit
  `task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1` opt-in after existing
  source admission. Same extractor, tool schema, one-attempt/call budgets,
  projection, refusal policy and evidence verifier.
- `src/linkloom/semantic_ingestion/__init__.py`: export both new public context
  types alongside the existing shared extractor API (independent review fix).
- `.gitattributes`: preserve bytes of **new** frozen public data and evidence.

No RawArtifact/parser/EvidenceSpan/offset/identity/hash policy, M1 field grounding,
veto, G operation, Runtime, product/HTTP configuration, M3-L frozen data or ledger,
old replay, Gold or scoring record is changed. Default normalized requests are
compared directly with the baseline extractor compiled from Git.

## Real Before / After evidence

| Input / output source | Before | After |
|---|---|---|
| Original raw second message | `索引迁移还包括补齐回归脚本。` in exact source window | Same source, plus canonical message map in new optional profile |
| SAME recorded raw model claims | `索引迁移`, `提交容量报告` | Same two descriptions; no invented supplementary task |
| Strict historical exact-request replay, all 3 sources | Recall 4/5, Precision 4/5 | Same 4/5 and 4/5; fields 24/24, evidence 5/5 |
| New D01 scripted explicit supplementary work | Separate original evidence for `修订告警手册` and `录制排障演示` | Same verifiable candidates; second message owner/date/project/vendor remain null |
| New task profile request | Absent | Independently versioned `task-discovery-cross-message/v1`, context `task-discovery-context/v1` |

Exact normalized requests, raw parts, output evidence, offsets and fingerprints:
`evidence/final-historical-diagnosis.json`. Exact historical replay request
comparison and scores: `evidence/historical-replay/`,
`m3j-replay.replay-comparison.json`, `m3j-replay.replay-score.json`.

## New public offline contracts

Data were frozen before production edits: 12 development cases / 13 positive
candidate observations; 14 independently designed public holdout cases / 14
positive candidate observations. Eight deliberately cropped quoted/cancelled/
conditional/suggestion outputs exercise refusal. Source texts, expected excerpts
and SHA-256 are in `data/` and `freeze_manifest.json`.

The positive output comes from Gold-shaped **oracle proposals**, not inference.
These metrics describe retained candidate observations under the Host contract.
They do not measure semantic discovery, generalization or real task identity.
The public holdout is independently authored, not a blind model evaluation.

| Contract metric | Dev Before → After | Holdout Before → After |
|---|---:|---:|
| Candidate Recall | 13/13 → 13/13 | 14/14 → 14/14 |
| Candidate Precision | 13/13 → 13/13 | 14/14 → 14/14 |
| False rejection / false acceptance | 0/0 → 0/0 | 0/0 → 0/0 |
| Erroneous scripted candidate grouping | 0 → 0 | 0 → 0 |
| Fields correct on matched candidates | 65/65 → 65/65 | 70/70 → 70/70 |
| Verified observation evidence | 13/13 → 13/13 | 14/14 → 14/14 |
| Fake calls / extra calls | 12 / 0 | 14 / 0 |

Field denominator includes owner, literal deadline, resolved deadline, project
and vendor, with nulls scored. Recall is separately reported so missed candidates
cannot disappear into matched-only field accuracy. The grouping count checks
scripted groups spanning distinct labelled candidates; it is not a measurement
of real-model grouping accuracy. No business merge is performed.

`evidence/before-contracts.json` and `final-contracts.json` contain every case,
request hashes, descriptions, fields, evidence and refusal reason. Scores are
identical with the SAME outputs, while new-profile request fingerprints differ
for all 26 cases. This demonstrates input separation and retained enforcement,
not a Recall increase. Real Provider requests in this milestone: **0**.

## Local verification

- Focused extraction/F/J checks: **157 passed**, `evidence/focused.xml`.
- Affected task/shared-semantic/source unit/integration regression: **608 passed**,
  `evidence/related.xml`.
- M3-L/M3-M source/budget/HTTP/product-chain critical regression: **215 passed**,
  `evidence/critical.xml`. This includes C1/C2/C3 normal Ask through the closed
  Host with Fake SDK, request counts 8/8/9, permissions, persisted ledgers,
  default-off/live adapter rejection, snapshot races and HTTP budget constraints.
- Final task-context, freeze and product evidence-scope checks after the last
  prompt clarification and extra guard test: **32 passed**,
  `evidence/final-context-freeze.xml` (14 M3-N tests included).
- Review fix public-import and affected shared contracts: **159 passed**,
  `evidence/review-fix.xml` (15 M3-N tests included).
- Exact historical replay: 3/3 sources unchanged, 0 paid calls.
- Protected-path diff and `git diff --check`: clean.

The suites overlap; their counts are not an aggregate distinct-test total.
All pytest runs used `-p scripts.evaluation_offline_plugin`, denying external
network transport. Localhost HTTP tests ran with required sandbox networking.
The independent new-contract runner installs the same transport denial.

Full unit/integration collection reports **4 baseline import errors**: three
MemoryAgentBench G3 tests need missing `scripts/run_memoryagentbench_g3_pilot.py`;
Stage C needs missing `scripts/run_stage_c_context_memory_loop.py`. Both are absent
from the baseline Git tree. `evidence/full-collection.xml` retains errors;
3061 tests were collected before the final additional M3-N test. No full-suite
pass is claimed. Existing pytest-asyncio configuration and pyreadline shutdown
warnings do not change successful pytest exit statuses. The first RED records
the missing interface; an intermediate test used a whole-source window for a
single canonical message and was corrected to the service's canonical window.
Intermediate evidence is retained; final evidence is identified above.

Reproduce using fresh outputs and a short Windows pytest base directory:

```powershell
$env:PYTHONPATH='src;.;tests/integration'
python scripts/evaluate_m3n_contracts.py --profile cross-message-v1 --output work/new-contracts.json
python -m pytest -p scripts.evaluation_offline_plugin tests/unit/test_task_discovery_m3n.py -q --basetemp=D:/webproject/Linkloom/work/n-new
```

## Remaining limit and next minimum step

No current interface blocker remains for a bounded per-source, cross-message
task prompt. Cross-source task identity, long-range ambiguous references,
semantic completeness and arbitrary cancellation interpretation remain outside
this contract. The existing conservative veto still has its documented bounded
grammar; it was not tuned against new cases. Grouping remains model-proposed,
all observations SUPPORTING_ONLY / UNCONFIRMED.

The new entry point is opt-in. Product/default and M3-L LIVE profiles are not
silently changed. The next minimum step is a separately authorized, budgeted
real Provider evaluation of the independently versioned profile on unchanged
public sets, with new raw artifacts and candidate/group/field/evidence scoring.
Only that evidence can support a semantic quality decision and subsequent
explicit profile adoption. This offline milestone does not authorize such calls.
