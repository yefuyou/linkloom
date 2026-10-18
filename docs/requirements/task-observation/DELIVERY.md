# M1 TaskObservation — local offline delivery

M1 implements task observations only. No task identity resolution, impact edges,
deadline revision inference, UI, new planner, graph service or provider dependency.
The pre-M1 report's 47 referenced local code paths were checked and all exist.

## Files and responsibilities

| File in `src/linkloom/semantic_ingestion/` | M1 change |
| --- | --- |
| `task_observation.py` (new) | TaskProposal, TaskObservation, field evidence, stable source-location identity, deterministic field/role validation and source read-back. |
| `task_observation_query.py` (new) | Read-only typed workspace/source/project/version queries, source currency checks and history. |
| `candidate_models.py` | Nullable PLAN/ACTION task proposal and source candidate observation sidecar; verifies their shared provenance/time boundary. |
| `extraction.py` | v2 extraction schema and task instructions; accepts historical recordings without the new field. |
| `validation.py` | Builds sidecars for PLAN/ACTION while keeping their decision valid-time bounds empty. |
| `candidate_persistence.py` | v3 sidecar serialization, continued v1/v2 decoding and identical-task retry comparison. |
| `materialization.py` | Task-only retry hook and shared v1/v2/v3 read-version gate. Existing Supporting Only policy remains authoritative. |

New tests are `tests/unit/test_task_observation.py` and
`tests/integration/test_task_observation_flow.py`. SPEC and implementation plan
record the user-authorized M1 boundary. The local exporter and evidence are under
`work/m1-task-observation/`; baseline copies preserve the five pre-existing dirty
ingestion files for delta review. No other existing code was edited for M1.

## Actual original-case output

Full structures, source identity/version, exact evidence offsets and original text:
[task-observations.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m1-task-observation/evidence-20261018-final/task-observations.json).
Two independent Chinese/English samples and their actual structures:
[independent-samples.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m1-task-observation/evidence-20261018-final/independent-samples.json).

| Observation | Structured description | Owner | Deadline | Explicit project | Explicit vendor |
| --- | --- | --- | --- | --- | --- |
| A, speech 2 | 短信接口接入；联调 | 陈晨 | 2026-10-14 | 星河项目 | unknown |
| B, speech 4 | 短信接口接入；按新供应商星帆通信的接口调整 | 陈晨 | 2026-10-18 | unknown | 星帆通信 |

A ID: `task_observation_v1_088aabbbc30838e95fe1791366d324024dc58244388d13a676cb987483ef4ddf`

B ID: `task_observation_v1_346c9975835fd0ed0a1b3e2177682c7a3419d7ab0bdc48b4697ab4ff656dd94e`

Both have `deadline_precision=DAY`, `deadline_role=TASK_DEADLINE`,
`authority=SUPPORTING_ONLY`, `relation_status=UNCONFIRMED`,
`extraction_status=EXTRACTED`, `validation_status=PARTIAL`.
PARTIAL reflects unknown vendor/project respectively, not extraction failure.
The export's `recorded_at=2026-10-18T09:00:00+00:00` is an explicitly synthetic
import time. `event_time` remains each source speech timestamp. Neither is a task
effective time. These are two observations, not confirmed distinct business tasks.

Original task statements:

> 我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调。

> 我继续负责短信接口接入，将按新供应商星帆通信的接口调整，计划在2026年10月18日前完成。

## Field verification

Each observation carries the existing immutable EvidenceSpan and per-field exact
Unicode character offsets (end exclusive). Description is a deterministic join
of source excerpts, not a free paraphrase. Named owners need a responsibility
role; first-person owners require both the explicit cue and the canonical parsed
speaker header, including multiline messages. Quoted/proposed/negated assignments
remain unknown. Deadline evidence includes the date's role, e.g.
`2026年10月14日前完成`, and cannot use document/event/import dates as a substitute.

`observation.verify(artifact)` verifies the source/version/hash/span, reads back
every field quote from that exact artifact, and revalidates roles and canonical
speaker. Both original observations and both independent samples return true.
Missing years retain MONTH_DAY precision and no resolved deadline; missing day
retains MONTH precision. No year/day is borrowed from metadata. Invalid or
ungrounded values become unknown with validation issues. Malformed/failed
extractions produce the existing REJECTED result with no candidate; old task
recordings lacking the new payload are explicitly NOT_PROVIDED, not invented.

## Persistence and read-only query

The v3 TaskObservation sidecar is persisted in the existing semantic_candidate
business payload, not only extraction audit JSON. No schema migration, new table,
or DecisionRecord write is needed. Existing candidate fingerprint and source
reference contracts continue to apply. A repeated identical capture preserves
the first recording time and extraction receipt; serialization/replay produces
no duplicate candidate or observation.

For an already workspace-authorized ProductApplication store:

```python
from linkloom.semantic_ingestion.task_observation_query import TaskObservationQuery

query = TaskObservationQuery(app.store)
current = query.list(workspace_id, source_id=source_id)
explicit_project = query.list(workspace_id, project="星河项目")
history = query.list(workspace_id, source_id=source_id, include_history=True)
```

Each result has `observation`, `candidate_ids`, `source_status` and
`extraction_conflict`. Project matching is normalized text equality, without
alias/entity inference. Thus the original project query returns A only; B is
still available by source and must wait for M2 to acquire a project relationship.
Conflicting extractions are flagged rather than silently selected.

Current reads require Supporting Only state and an exact registered evidence
version/hash. History preserves older versions and labels unmatched versions
NOT_CURRENT. A freshly opened bare store with no current source registry cannot
assert currency; use `include_history=True` to inspect its retained records.
The product's existing source catalog reconstructs its registry normally.

Reproduce the local demo with a **new** output folder name:

```powershell
python work/m1-task-observation/export_observations.py work/m1-task-observation/my-new-demo
```

The exporter uses recorded responses and creates only its own synthetic SQLite
files. The output confirms two observations after reopening, successful normal
materializer read-back, zero formal decisions without review, and an unchanged
database hash across read-only queries.

## Validation evidence

- Final consolidated batch after review fixes: **190 passed**, exit zero — M1
  tests plus existing semantic extraction/prompt, candidate materialization,
  ordinary documents, Agent Memory lifecycle, temporal Decision Memory,
  structured lookup, source identity/parser, candidate migration, semantic
  quality and product segment recovery. Earlier incremental runs (139 and 63)
  overlap with this batch and are not added to its count.
- Original five-line acceptance verifies zero decisions before approval, blocked
  task materialization, explicit approval for supplier decisions, historical
  lookup and a correct supplier supersession link.
- Two independent Markdown/TXT samples verify field read-back, serialization,
  offline replay, genuine re-extraction with new receipt/import time, reopen,
  source version history and old-version exclusion from current results.
- No-year, missing-field, wrong-role, invalid date, fabricated field, proposed/
  negated/quoted responsibility, forged speaker and multiline speech cases are
  covered. `git diff --check` passed on the five touched existing source files.
- Independent review found a missing normal-read v3 gate, wrong evidence offsets
  for repeated mentions, negated entity mentions and truncated owner acceptance.
  Each was reproduced with a failing test and fixed. Normal task read-back now
  succeeds; blocked materialization is specifically APPROVAL_MISSING, not a
  payload decoding error. Role-bound matches retain their actual source offsets.
- Independent bounded re-review: **PASS**, no remaining Required/Critical findings
  in the requested scope. Reviewer performed read-only inspection and did not run
  tests or alter any files/processes.

Tests use pytest with plugin autoload disabled and unique workspace basetemp
folders. No whole-suite evaluation/live-provider run was performed. The Python
environment prints a pyreadline3 console destructor warning on exit; pytest
reported the pass counts above and exited zero.

## Remaining boundary

M2 still needs evidence-backed task identity, project/context linkage across
statements/documents, supplier-change impact relations, plan revision relations,
and a query/verification contract for joining those facts. M1 neither computes a
four-day delay nor claims that all affected technical tasks have been found.
Real provider extraction quality is untested; the new contract was verified with
offline scripted/recorded responses only. Role/date validators deliberately use
bounded Chinese/English patterns and leave unsupported wording unknown. Project
queries currently filter workspace candidate payloads in Python; a project index
can be added if corpus size makes that necessary.

Original databases, running processes, frozen Gold, existing evaluation ledgers,
UI, Runtime/RAG and unrelated working-tree changes were left alone. No commit,
push or PR was created. M1 stops here.
