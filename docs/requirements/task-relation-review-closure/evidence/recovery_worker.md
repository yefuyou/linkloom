# Recovery and host-composition RED/GREEN receipt

Scope: the approved backend safety slice only.  No parser, `TaskRelationStore`,
`RelationQuery`, static UI, frozen input, Gold data, commit, push, or PR was
changed by this worker.

## RED

The new loopback recovery case reached the intended injected failure boundary:
the first `CONTINUE` returned `409 BLOCKED`, persisted a `PENDING` audit, and
the source proof sidecar contained the newly captured rows.  The exact retry
then returned `409 TASK_PREPARATION_UNAVAILABLE` because the original Runtime
ledger rejected the newly appended independent proof rows.  This exposed the
missing exact-run rebuild fallback rather than permitting a duplicate proof or
false commit.

## GREEN

Final verification, using a short project-local pytest basetemp to avoid
Windows `MAX_PATH` failures from the long visualization path:

```text
python -m pytest -q --basetemp work/pytest-recovery-short -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_recovery.py tests/unit/test_task_review_backend.py
8 passed in 19.95s

python -m pytest -q --basetemp work/pytest-http-final -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_http_acceptance.py
2 passed in 15.40s

python -m py_compile src/linkloom/ui/server.py src/linkloom/ui/task_review.py src/linkloom/ui/__main__.py tests/integration/test_task_review_recovery.py
PYCOMPILE_PASS
```

The recovery case now retries the identical request/capability to `200
COMMITTED`, keeps revision `1`, and confirms the proof-sidecar assessment,
comparison, and event counts are unchanged.  The catalog-revoke case keeps
content and content hash unchanged while returning redacted metadata only; the
empty durable Product starts without creating review or relation sidecars.  An
in-memory Product with no explicit durable review sidecar is intentionally
non-writable rather than falling back to a process-global file.

This is worker evidence only; independent milestone acceptance remains with
the parent/reviewer.

## Final narrow additions

The exact cross-run history test first failed because a new run returned no
old statement.  It now exposes only same-workspace rows whose source snapshot,
source catalog, and step pair hash all match; rows are marked
`current_run=false` and never enter current-run revision/CAS.  Foreign
workspace, pair mismatch, and changed-source reads remain hidden.

The independent commit-boundary test also found that a receipt written before
`COMMITTED` audit failure was incorrectly rejected with capability `403`.  The
repair path now validates the original PENDING capability binding, requires
the live proof head to equal the durable receipt head, returns `409 BLOCKED`
while audit repair is still failing, and returns `200 COMMITTED` only after the
single audit repair succeeds without replaying proof.

The deterministic GET projection now exposes `source_verdict`,
`source_verdict_source`, source support/counter evidence, source blockers, and
`source_proof_status` separately from the RelationQuery proof state.  Model
proposal evidence remains in its original fields and is not treated as a
canonical verdict.

The normal committed-retry boundary is covered as well: the original
capture-time capability can replay the same committed request repeatedly while
the proof head is unchanged; a real append-only lifecycle event changes that
head and prevents a `200 COMMITTED` replay.

The final review delta removes the old generic CONTINUE `except: pass` path:
an existing CONTINUE receipt is now either safely repaired/replayed or returns
explicit `409 BLOCKED`.  The fresh source-proof capability plus the old request
body is not allowed to create a false success.  `source_proof_status` is
re-derived after RelationQuery lifecycle readback, so a later `STALE`/`RETRACTED`
event exposes `BLOCKED` even when `source_verdict` remains the deterministic
assessment diagnosis.

Additional verification:

```text
python -m pytest -q --basetemp work/pytest-commit-boundary2 -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_commit_boundary.py
2 passed in 15.01s

python -m pytest -q --basetemp work/pytest-related-green4 -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_recovery.py::test_new_run_projects_only_exact_related_review_history
1 passed in 7.88s

python -m pytest -q --basetemp work/pytest-commit-boundary3 -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_commit_boundary.py tests/integration/test_task_review_http_acceptance.py
4 passed in 21.33s

python -m pytest -q --basetemp work/pytest-commit-boundary-final -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_commit_boundary.py
2 passed in 17.81s

python -m pytest -q --basetemp work/pytest-final-backend2 -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_recovery.py tests/integration/test_task_review_commit_boundary.py tests/integration/test_task_review_http_acceptance.py tests/unit/test_task_review_backend.py
13 passed in 43.86s

python -m pytest -q --basetemp work/pytest-review-final-delta -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_commit_boundary.py
2 passed in 19.75s

python -m pytest -q --basetemp work/pytest-review-http-delta -p no:pytest_asyncio -p no:asyncio tests/integration/test_task_review_http_acceptance.py tests/integration/test_task_review_recovery.py::test_new_run_projects_only_exact_related_review_history tests/unit/test_task_review_backend.py
7 passed in 15.12s
```
