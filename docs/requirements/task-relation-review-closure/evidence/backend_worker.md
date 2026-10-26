# Backend worker RED/GREEN receipt

Scope: `task_review.py`, `server.py`, `__main__.py`, and new Python tests only.
No proof parser, `TaskRelationStore`, `RelationQuery`, static UI, frozen inputs,
Gold data, commit, push, or PR was changed by this worker.

## RED

Before the implementation, the new contract test could not import
`linkloom.ui.task_review` (`ModuleNotFoundError`). This was the intended
durability/API seam failure, not a baseline proof-contract failure.

## GREEN

The following commands were run with the worktree `src` on `PYTHONPATH` and
pytest's asyncio plugins disabled because this feature uses the synchronous
loopback server:

```text
python -m pytest -q -p no:pytest_asyncio -p no:asyncio --basetemp work/pytest-task-review-unit-3 tests/unit/test_task_review_backend.py
4 passed in 2.68s

python -m pytest -q -p no:pytest_asyncio -p no:asyncio --basetemp work/pytest-task-review-http-3 tests/integration/test_task_review_http_acceptance.py tests/unit/test_task_review_backend.py
4 passed in 54.10s

python -m pytest -q -p no:pytest_asyncio -p no:asyncio --basetemp work/pytest-ui-http tests/integration/test_ui_http_server.py
5 passed in 6.56s
```

The HTTP contract covers real loopback GET/POST, durable REVIEW, exact retry,
request collision, authority-field rejection, foreign Origin/workspace,
source-update redaction, and the unchanged proof state. The sidecar unit
contract covers restart history, append-only blocked audit records, and
concurrent revision CAS.

The backend also binds capabilities to the current proof-sidecar event chain;
an external lifecycle event makes a stale preview return `409 STALE_PROOF_HEAD`.
CONTINUE remains source-only and can return explicit `BLOCKED`; local review
never establishes business approval or overrides a relation verdict. This is a
worker evidence receipt, not the independent milestone acceptance decision.

Final backend-focused rerun after metadata immutability and proof projection:

```text
python -m pytest -q -p no:pytest_asyncio -p no:asyncio --basetemp work/pytest-task-review-final tests/unit/test_task_review_backend.py tests/integration/test_task_review_http_acceptance.py
6 passed in 13.62s
```
