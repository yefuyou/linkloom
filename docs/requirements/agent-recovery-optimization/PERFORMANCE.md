# Agent recovery performance baseline

This is a read-only, synthetic baseline for the recovery optimization boundary.
It measures the existing `scripts/reliability_scale.py` scenario through the
thin `scripts/recovery_scale.py` observer. The observer adds an exclusive
`verifiedread` bucket; it does not change `src/` behavior or inject a Provider.

The run used `D:/Anaconda/python.exe`, `LINKLOOM_RETRIEVAL_MODE=bm25`, synthetic
SQLite workspaces, and synthetic Markdown notes only. The initial subprocess
did not resolve the existing `rank_bm25` package, so that diagnostic used the
user-site path. Final processes resolve it with `PYTHONPATH=src;.` normally;
no package was installed.
No real Vault, remote Provider, Git operation, or user note was involved.

## Command and evidence

From the recovery optimization worktree:

```powershell
$env:PYTHONPATH = 'src;C:\Users\18019\AppData\Roaming\Python\Python313\site-packages'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
& 'D:\Anaconda\python.exe' scripts/recovery_scale.py `
  --output work/agent-recovery/scale-before
```

The output contains `summary.json` and one directory per source count. Each
source count has three repeats. The first complete run is retained under
`work/agent-recovery/scale-before`; the focused 500-source diagnostic rerun is
under `work/agent-recovery/diagnostic-500`, and its captured command output
records the safe error envelope.

The initial stage buckets are **not additive**: retrieval includes nested
`read_notes`, so adding these columns double counts work. The derived `other`
bucket is unreliable and must be read as N/A regardless of its retained
numeric diagnostic value. The final observer records retrieval inclusive and
nested-read time separately, then reports exclusive retrieval. Original
measurements below remain intact. `read_notes` includes every
`VaultReader.read_notes` call. `verifiedread` measures only the exclusive work
inside `_read_verified_note_for_tool` after subtracting its nested full source
read; its `inclusive_ms` and `nested_read_notes_ms` fields show the overlap for
diagnosis. `other` is the remaining e2e time. `e2e` spans
`ProductInvestigationService.start` through the final `inspect`.

## Baseline medians

| Synthetic notes | Runs | Result | Snapshot ms | Scan ms | Read notes ms | Retrieval ms | Verified read exclusive ms | Other ms | E2E ms |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | 3/3 | success | 43.86 | 42.35 | 599.18 | 111.03 | 0.85 | 3,259.60 | 4,067.53 |
| 100 | 3/3 | success | 329.73 | 173.98 | 8,001.83 | 1,271.21 | 1.21 | 3,699.59 | 13,472.72 |
| 500 | 3/3 | `MODEL_ADAPTER_FAILED` | 3,048.69 | 2,017.61 | 32,937.52 | 0 | 0 | 9,127.34 | 45,624.75 |

The 10- and 100-source runs completed the two searches and three verified
reads, with `read_notes` called 12 times per run and no tool errors. The
500-source fixture reached the existing 30-second `SourceOnlyController`
deadline before the first search; the diagnostic rerun records the same safe
`MODEL_ADAPTER_FAILED` result for all three repeats. Its source preparation and
the first three full reads are still valid scale observations, but its e2e
completion number is a harness ceiling rather than a successful product run.

The key signal is the repeated full-source work:

- At 10 sources, full reads consume about 599 ms of a 4.07 s e2e run.
- At 100 sources, full reads consume about 8.00 s of a 13.47 s e2e run.
- At 500 sources, the first three reads alone consume about 32.94 s before the
  fixed offline controller deadline is reached.
- Verified-read exclusive work is approximately 0.9–1.2 ms. Its inclusive
  time is about 0.16 s at 10 sources and 2.74 s at 100 sources, almost entirely
  nested `read_notes` time.

## Comparable 500-source baseline

The successful 500-source comparison uses the same synthetic scenario and
instrumentation, with only the fake `SourceOnlyController` stopwatch extended
from 30 to 180 seconds by `scripts/recovery_scale.py --fake-only-deadline
180`. This is an experiment-only controller setting; it
does not change production `max_steps`, recovery policy, or model limits. The
valid result is retained under
`work/agent-recovery/scale-before-comparable-r2/summary.json` because an
earlier same-name attempt was stopped by a temporary harness sign error and
must not be used as evidence. All rows below are 3/3 successful repeats, with
no Provider calls, two searches, three verified reads, and 12 `read_notes`
calls per run.

| Synthetic notes | Deadline s | Snapshot ms | Scan ms | Read notes ms | Retrieval ms | Verified read exclusive ms | Other ms | E2E ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 180 | 109.73 | 126.24 | 1,702.30 | 287.30 | 1.74 | 9,385.44 | 11,704.68 |
| 100 | 180 | 528.57 | 246.00 | 11,905.04 | 1,943.98 | 1.35 | 5,209.37 | 19,757.18 |
| 500 | 180 | 3,989.65 | 1,580.60 | 110,670.05 | 18,329.98 | 2.97 | 2,380.54 | 137,034.85 |

The 500-source completion is therefore comparable for scale analysis, while
the 30-second run remains useful as the original bounded-failure observation.
The experiment still uses synthetic SQLite/Markdown data only.

## Current P0/P1 after baseline

After the current P0/P1 changes, the same observer was run with the same
synthetic fixtures and the same 180-second fake-only controller deadline:

```powershell
$env:PYTHONPATH = 'src;C:\Users\18019\AppData\Roaming\Python\Python313\site-packages'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
& 'D:\Anaconda\python.exe' scripts/recovery_scale.py `
  --fake-only-deadline 180 `
  --output work/agent-recovery/scale-after-core
```

The result is retained in
`work/agent-recovery/scale-after-core/summary.json`. Every scale completed 3/3
successfully with no Provider calls, two searches, three verified reads, 12
`read_notes` calls, and zero tool errors. No P2 production performance fix was
applied during this measurement, so these numbers are an after-core profile,
not evidence of an optimization or a speedup.

| Synthetic notes | Deadline s | Snapshot ms | Scan ms | Read notes ms | Retrieval ms | Verified read exclusive ms | Other ms | E2E ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 180 | 20.97 | 21.38 | 417.13 | 63.92 | 0.62 | 2,378.98 | 2,905.72 |
| 100 | 180 | 186.04 | 86.62 | 4,029.56 | 518.13 | 0.82 | 2,110.59 | 7,369.46 |
| 500 | 180 | 2,109.23 | 1,013.83 | 93,921.49 | 14,859.02 | 3.02 | 3,386.95 | 115,603.22 |

## Load variation and comparison limits

The before and after medians are descriptive profiles only. They were run at
different times and are not a controlled A/B experiment: OS scheduling,
temporary-directory and SQLite I/O, Python warm-up/GC, process RSS, and local
model-loop timing can move individual repeats. The 500-source after run also
has a wide repeat range (about 62.2 s, 115.6 s, and 160.6 s e2e), so a lower
or higher median must not be attributed to a P2 change. The repeat counts and
stage definitions are comparable; the measurement does not establish a causal
performance delta. The 180-second deadline is only the synthetic controller
override in this script and does not alter production `max_steps` or policy.

## Future cache design note (not implemented)

If a later milestone proves that a cache is needed, keep the first version
small and explicitly scoped. A cache key should include all of:
`workspace_id`, `source_id`, `source_version`, `content_sha256`, the index
fingerprint or `index_sha256`, retrieval `backend`, and document `schema`
version. Entries must be isolated by workspace and source identity; no entry
may cross workspaces or source versions. Invalidate on any key change, a
path-set/content-hash mismatch, an index/schema/backend change, or an
unresolved freshness check. Evidence validation and the final source gate stay
in force. Prefer a run-local snapshot first; a process-wide cache requires an
explicit lifecycle, ownership, eviction, and invalidation contract. This is a
future design boundary only; no cache or P2 performance fix was added here.

## Root cause

`VaultReader.read_notes()` parses and verifies every indexed Markdown document
on every call. The runtime currently wires that full read into several
boundaries. The relevant locations are:

1. `RuntimeRunBackend.__init__` preloads `_documents` for UI projection
   (`src/linkloom/ui/backend.py:60-81`).
2. `RuntimeEngine.start_multi_agent` reads through the adapter before starting
   the durable loop (`src/linkloom/runtime/graph.py:518-521`).
3. The resolve-source and retrieve-context nodes each instantiate a reader and
   read the full source set (`src/linkloom/runtime/graph.py:1363-1368` and
   `1400-1402`).
4. `RuntimeAgentAdapter._read_documents()` deliberately calls
   `self.reader.read_notes()` at every tool boundary
   (`src/linkloom/agents/runtime_adapter.py:149-152`). Its retrieval document
   provider is wired at `runtime_adapter.py:102-110`, and verified-read
   dispatch at `runtime_adapter.py:496-513` and `521-532` repeats the same
   full read. The verified-read path has only a sub-millisecond amount of
   exclusive work.
5. `RuntimeRetrievalBackend.search()` asks that provider for documents on each
   search (`src/linkloom/retrieval_v2/runtime_backend.py:116-147`); its existing
   fingerprint/index reuse (`:322-365`) cannot avoid the provider's repeated
   Markdown parsing.

The observed 12 calls per successful run are the measurable effect of these
overlapping lifecycle, retrieval, and evidence paths. The exact total can vary
when the model loop stops early, but the source work remains O(number of notes)
per boundary instead of O(number of notes) per run.

## Smallest safe optimization path

Within a later performance boundary, a candidate is one run-local source
snapshot across the existing loader,
adapter, and graph boundary: load and validate `NoteDocument` values once,
retain the existing index/root fingerprint with that snapshot, and pass the
snapshot to the adapter's document provider and verified-read callbacks. The
candidate locations are:

1. `src/linkloom/agents/runtime_adapter.py:102-110,149-152`: add a run-local
   tuple of source documents and a small `prime_documents(...)`/provider path.
2. `src/linkloom/runtime/graph.py:518-521,1363-1368,1400-1402`: prime it once
   at the existing start boundary and let resolve/retrieve consume the same
   validated snapshot.
3. `src/linkloom/loader.py:183-212`: add a read-only validator for a cached
   tuple that checks the current indexed path set and hashes every current
   document against `NoteDocument.content_sha256` before the tuple is reused.

The third item is the safety requirement: this must not be a blind cache. A
`NoteDocument` uses a frozen dataclass (`src/linkloom/schemas.py:37`, with its
content hash and line count at `:45-46`), so a run-local tuple can remove
repeated parsing and object construction. The frozen dataclass prevents field
rebinding; its list-valued metadata must also be treated as read-only (or
copied to immutable values) when the tuple is shared. Neither form proves the
files stayed unchanged. Each reuse must therefore retain the full
content-hash/path-set check; on mismatch, the snapshot is rebuilt or the run
is rejected as stale. This preserves the current freshness semantics while
avoiding repeated Markdown parsing. The existing final source gate in
`graph.py:1492-1504` and the resume check around `graph.py:968-972` remain
mandatory. Evidence identity, content hash, quote hash, and line-location
checks also remain in `_read_verified_note()`.

`ProductInvestigationService.start` exports rows into a job-owned
`TemporaryDirectory` (`src/linkloom/ui/investigation.py:144-173`), and the
`_InvestigationJob` retains that temporary root. That makes the product
snapshot a practical read-only input for the job, but it is not a permission
to assume that every `RuntimeEngine` source is immutable. The explicit
snapshot token plus the full hash/path validation and final freshness gate are
still required for generic runtime use.

If the first patch is insufficient, the next two bounded options are:

1. Reuse the same snapshot-backed provider for all retrieval searches so the
   BM25 index sees one source fingerprint and does not trigger another full
   Markdown read for each search.
2. Remove only the duplicate resolve-source/retrieve-context reads once their
   callers consume the validated snapshot; keep the explicit run-start and
   resume freshness checks as the safety boundary.

Do not change evidence policy, source identity, tool limits, or recovery
classification as part of this optimization. The baseline does not justify a
new cache subsystem, background watcher, or vector-store migration.

## Final exclusive observer closure

The initial observer double counted `read_notes` nested inside retrieval.
This was found during root integration, before Git delivery. The corrected
observer now subtracts nested reads from both retrieval and verified-read
buckets. Historical raw summaries remain unchanged; their `other` buckets
are N/A. No production behavior changed. A new current-code run was saved at
`work/agent-recovery/scale-final-exclusive/summary.json`, again three repeats
at every size, all nine successful with verified evidence and no tool errors.

| Notes | Snapshot ms | Scan ms | read_notes ms | Retrieval exclusive ms | Verified read exclusive ms | Other ms | E2E ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 76.36 | 95.69 | 1,851.07 | 42.26 | 2.23 | 10,622.73 | 12,716.11 |
| 100 | 555.30 | 299.76 | 14,949.61 | 130.06 | 1.90 | 9,477.91 | 25,180.92 |
| 500 | 973.04 | 1,300.18 | 90,085.21 | 737.88 | 2.73 | 10,660.89 | 104,234.32 |

Each row reports the median of each stage independently, so median columns
need not add to median E2E. Per-run exclusive columns do add to E2E; this is
checked in the sanitized summary projection. The 500-note E2E repeats are
192.45, 68.10 and 104.23 seconds. The fake-controller 180-second stopwatch
does not include all Product startup/preparation time and is not an E2E
promise. Again this variance establishes the bottleneck, not a causal speedup.
All runs still perform 12 full reads, two searches and three verified reads.

## Limits and acceptance status

This document is evidence for the optimization boundary, not a claim of a
production fix. Both the original bounded baseline and the separate
180-second comparable baseline are complete for the requested three repeats at
10, 100, and 500 synthetic sources. The required production optimization has
not yet been applied in this read-only audit.
