# Fresh 249-node differential regression

Exact commits: baseline `37e45b169f19c4edd29db59f637e690caab31ad6`, candidate `499bb3733bb10c0fc5c3145da9b8a9d3d587274d`.

| Result | Baseline | Candidate |
|---|---:|---:|
| Requested / reported nodes | 249 / 249 | 249 / 249 |
| Raw pytest passed | 209 | 209 |
| Raw pytest failed | 40 | 40 |
| Supported-input assertion failures | 2 | 2 |
| Historical input unavailable | 38 | 38 |
| Skipped / unexecuted / environment | 0 | 0 |

Differential classification: 209 stable passes, 2 original failures, 38 unexecutable, 0 new failures, 0 fixed. **The historical core suite is not green.** Raw failures remain in the XML; unavailable inputs do not become passes or exclusions from the denominator.

The two original failures are:

- `test_verified_current_conflict_guides_final_only_after_both_source_reads`: historical-conflict recovery expects CONTINUE, gets FINAL_PARTIAL at both revisions.
- `test_gemini_request_serializes_verified_context_into_model_visible_input`: expects a passage quote in a search-only model context; both revisions omit it.

This infrastructure task does not change either production behavior or these assertions. Their semantics require a separate product/test decision before claiming a fully green historical baseline.

The 38 unavailable nodes depend on 11 missing `work/phase9_gemini`, `work/phase11`, and `work/phase14_gemini` artifacts. Neither requested commit contains those blobs, reachable Git history yielded no originals, and a read-only main-checkout probe found no originals. No replacement was synthesized. `INPUT_PROVENANCE.json` lists every missing path and affected node.

Both sides used the same fresh Python environment and pinned dependencies, serial execution, explicit BM25 retrieval, short Windows source/temp paths, no ambient pytest plugins, no Provider credentials, and an external-transport blocking fixture. Git-archive source bytes were verified after execution: 891 baseline and 922 candidate tracked files, zero changes. The comparison records all 39 changed paths and their before/after blob IDs.

Earlier full diagnostic runs exposed overlong Windows artifact paths, sandbox socket restrictions and local transport timeouts. They remain retained and are inventoried in `DIAGNOSTIC_RUNS.json`. The authoritative full rerun is `full249-qualified-env`; it has no unexplained missing nodes or environment errors. Raw JUnit, logs and hashes are under `evidence/`. Classification is tested separately so an unknown error, skipped test or unavailable input cannot become green.

The new synthetic Core Gate is a separate executable acceptance suite. Its 41 passing checks cannot replace the above 249-node result.
