"""Typed LangGraph state of one orchestration run.

The LangGraph checkpoint owns this state (task graph, blackboard, rounds, answer). The Runtime checkpoint of
each Worker only records that Worker's own execution and is never read to decide what the orchestration does next.

Blackboard layers, kept apart on purpose:
* raw_retrievals: what a Worker's tools returned (not yet judged).
* verified_evidence: evidence of a result that passed the Reviewer gate (identity, version, hashes, owner task).
* inferences: what an Agent concluded. Never written to Decision Memory by this package.
* approved_decisions: written only from a human interrupt resume.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

PENDING, RUNNING, ACCEPTED, ABSTAINED, FAILED, SKIPPED = (
    'pending', 'running', 'accepted', 'abstained', 'failed', 'skipped')
TERMINAL = frozenset({ACCEPTED, ABSTAINED, FAILED, SKIPPED})
KEEP = frozenset({ACCEPTED, ABSTAINED})   # a reviewed result is never overwritten
_RANK = {PENDING: 0, RUNNING: 1, ACCEPTED: 2, ABSTAINED: 2, FAILED: 2, SKIPPED: 2}


def _order(task):
    return (task.get('epoch', 0), _RANK[task['status']], task.get('attempt', 0))


def merge_tasks(left, right):
    """Merge by task id. Status only moves forward inside an epoch; a reviewed result is never replaced.

    A retry starts a new epoch (the Host resets a retryable failure), so it can legitimately replace FAILED.
    """
    merged = dict(left or {})
    for task_id, task in (right or {}).items():
        current = merged.get(task_id)
        if current is None:
            merged[task_id] = dict(task)
        elif current['status'] in KEEP:
            continue
        elif _order(task) >= _order(current):
            merged[task_id] = dict(task)
    return merged


def merge_first(left, right):
    """Keep the first value written under a key. Shared evidence is never silently replaced."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        merged.setdefault(key, value)
    return merged


def first_reason(left, right):
    return left or right


def merge_unique(left, right):
    seen = {repr(item) for item in (left or [])}
    merged = list(left or [])
    for item in right or []:
        if repr(item) not in seen:
            seen.add(repr(item))
            merged.append(item)
    return merged


class OrchestratorState(TypedDict, total=False):
    # Host input. confirmed_scope is what the Host has authorized and confirmed; entity_leads are names the
    # question mentioned that are not confirmed. Workers resolve leads by source evidence, never by assumption.
    query: str
    confirmed_scope: dict[str, Any]
    entity_leads: list[str]
    deadline_at: float
    required_items: dict[str, str]
    judgments: list[dict]
    # Task graph and blackboard
    tasks: Annotated[dict[str, dict], merge_tasks]
    rows: Annotated[dict[str, dict], merge_first]
    raw_retrievals: Annotated[dict[str, dict], merge_first]
    verified_evidence: Annotated[dict[str, dict], merge_first]
    inferences: Annotated[dict[str, dict], merge_first]
    approved_decisions: Annotated[list, merge_unique]
    # Control
    round: int
    dispatches: int
    review_pending: bool
    confirmation: dict | None
    abort_reason: Annotated[str | None, first_reason]
    trace: Annotated[list, operator.add]
    # Result
    status: str
    answer: dict | None


class WorkerState(TypedDict, total=False):
    """Private context of one Worker subgraph run (its own input and result)."""
    task: dict
    upstream: list
    context: dict
    receipt: dict | None
    outcome_row: dict | None
    outcome_code: str | None
    outcome_observed: dict
    # outputs merged into the parent state
    tasks: Annotated[dict[str, dict], merge_tasks]
    rows: Annotated[dict[str, dict], merge_first]
    raw_retrievals: Annotated[dict[str, dict], merge_first]
    verified_evidence: Annotated[dict[str, dict], merge_first]
    inferences: Annotated[dict[str, dict], merge_first]
    abort_reason: Annotated[str | None, first_reason]
    trace: Annotated[list, operator.add]
