"""LLM Branch Planner: the model proposes two branches, the Host validates every field.

The model output is a proposal only. Nothing here grants a tool, an entity, a date or a budget.
Any rejection raises PlanRejected and the caller degrades to the template split.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from types import SimpleNamespace

from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
from linkloom.runtime.task_plan import TaskPlan, validate_model_proposed_plan
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.tools.contracts import ToolDefinition

TOOL_ID = 'submit_branch_plan'
VERSION = 'branch-plan/v1'
PLANNER_OUTPUT_TOKENS = 4096
_TRUNCATED = frozenset({'length', 'max_tokens'})
_STEP = dict(type='object', properties=dict(id=dict(type='string'), question=dict(type='string'),
    depends_on=dict(type='array', items=dict(type='string'))), required=['id', 'question', 'depends_on'])
_BRANCH = dict(type='object', properties=dict(question=dict(type='string'), entity_text=dict(type='string'),
    time_text=dict(type='string'), steps=dict(type='array', items=_STEP)),
    required=['question', 'entity_text', 'time_text', 'steps'])
_SCHEMA = dict(type='object', properties=dict(decline=dict(type='boolean'), reason=dict(type='string'),
    branches=dict(type='array', items=_BRANCH)))
_PROMPT = (
    "Split the user question into exactly two independent read-only investigation branches. Each branch has a "
    "self-contained question, entity_text and time_text copied verbatim from the user question (time_text may be "
    "empty), and ordered steps. Steps depend only on earlier steps of the same branch; the first step has no "
    "dependency and a later step refers to the earlier answer with the placeholder {verified_entity}. The two "
    "branches must not depend on each other. If the question cannot be split, call the tool with decline=true "
    "and a short reason. Reply only by calling submit_branch_plan.\nuser_question=")


class PlanRejected(ValueError):
    """The Planner output is unusable; the message is a stable reason code."""


@dataclass(frozen=True)
class PlannerScope:
    query: str
    as_of: str
    max_steps: int
    blocked: tuple = ()
    max_requests: int = 0
    max_output_tokens: int = 0


def branch_plan_request(query):
    stamp = canonical_hash(query)[:24]
    schema = deepcopy(_SCHEMA)
    return ModelTurnRequest(run_id='branchplan_' + stamp, turn_id='branchturn_' + stamp,
        task_id='branchtask_' + stamp, agent_id='branch-planner', sequence=1,
        user_input=_PROMPT + json.dumps(query, ensure_ascii=False), observation=None,
        available_tools=[ToolDefinition(tool_id=TOOL_ID, version=VERSION,
            description='Propose two independent branches or decline.', input_schema=schema,
            output_schema=deepcopy(schema))],
        generation_options=ModelGenerationOptions(max_output_tokens=PLANNER_OUTPUT_TOKENS,
                                                  require_tool_call=True))


def _tool_arguments(response):
    if response is None or response.error is not None:
        raise PlanRejected('PLANNER_PROVIDER_ERROR')
    if str(response.finish_reason or '').lower() in _TRUNCATED:
        raise PlanRejected('PLANNER_TRUNCATED')
    if response.proposal is not None:
        calls = [item.runtime_call for item in response.proposal.tool_calls]
    elif response.action is not None and response.action.kind == 'tool_call':
        calls = [response.action.tool_call]
    else:
        calls = []
    if len(calls) != 1 or calls[0].tool_id != TOOL_ID:
        raise PlanRejected('PLANNER_NO_TOOL_CALL')
    return calls[0].arguments


def _text(value, limit=512):
    if not isinstance(value, str) or len(value) > limit:
        raise PlanRejected('PLANNER_SCHEMA_INVALID')
    return value


def _rows(payload):
    if not isinstance(payload, dict) or set(payload) - {'decline', 'reason', 'branches'}:
        raise PlanRejected('PLANNER_SCHEMA_INVALID')
    if payload.get('decline') is True:
        raise PlanRejected('PLANNER_DECLINED')
    if payload.get('decline') not in (None, False) or not isinstance(payload.get('branches'), list) \
            or len(payload['branches']) != 2:
        raise PlanRejected('PLANNER_SCHEMA_INVALID')
    rows = []
    for branch in payload['branches']:
        if not isinstance(branch, dict) or set(branch) != {'question', 'entity_text', 'time_text', 'steps'}:
            raise PlanRejected('PLANNER_SCHEMA_INVALID')
        steps = branch['steps']
        if not isinstance(steps, list) or not 1 <= len(steps) <= 6:
            raise PlanRejected('PLANNER_SCHEMA_INVALID')
        goals = []
        for step in steps:
            if not isinstance(step, dict) or set(step) != {'id', 'question', 'depends_on'} \
                    or not isinstance(step['depends_on'], list):
                raise PlanRejected('PLANNER_SCHEMA_INVALID')
            goals.append(dict(subgoal_id=_text(step['id'], 64), question=_text(step['question']),
                              depends_on=[_text(item, 64) for item in step['depends_on']]))
        rows.append((_text(branch['question']).strip(), _text(branch['entity_text'], 160),
                     _text(branch['time_text'], 160), goals))
    return rows


def _check_grounding(question, entity, time_text, steps, scope):
    """Check the Host-derived root question of a branch. The model's own branch question is never executed."""
    from linkloom.ui.router_context import blocked_entity_reason
    from linkloom.ui.scope_discovery import resolve_query_date
    from linkloom.ui.structured_router import RouterInvalid, mention_spans, resolve_time_text
    if not question or not mention_spans(scope.query, entity) or not mention_spans(question, entity):
        raise PlanRejected('PLANNER_ENTITY_NOT_LITERAL')
    if (blocked_entity_reason(entity, scope.blocked)
            or any(blocked_entity_reason(text, scope.blocked) for text in (question, *(s['question'] for s in steps)))):
        raise PlanRejected('PLANNER_ENTITY_BLOCKED')
    context = SimpleNamespace(query=scope.query, time_options={}, required_as_of=None)
    try:
        resolve_time_text(context, time_text, 'UNRESOLVED' if time_text.strip() else 'NONE')
    except RouterInvalid:
        raise PlanRejected('PLANNER_TIME_NOT_LITERAL') from None
    # A branch may not carry a date other than the trusted scope date.
    for text in (question, time_text):
        date, error = resolve_query_date(text, scope.as_of if text is question else None)
        if error or (date and date[:10] != scope.as_of[:10]):
            raise PlanRejected('PLANNER_TIME_CONFLICT')


def validate_branch_plan(payload, scope):
    """Return two (question, TaskPlan | None) pairs derived by the Host, or raise PlanRejected.

    The model's branch question is only shape-checked. The executed question and plan come from the combined
    plan that passed validate_model_proposed_plan, exactly as the template path derives them.
    """
    from linkloom.ui.parallel_investigation import independent_subplans
    rows = _rows(payload)
    goals = [goal for row in rows for goal in row[3]]
    try:
        combined = TaskPlan.from_dict(dict(goal=scope.query.strip(), subgoals=goals))
        # One shared grouping rule: a dependency across branches merges them and leaves fewer than two.
        groups = independent_subplans(combined, 2 * len(goals) + 1)
        if len(groups) != 2 or any((len(plan.subgoals) if plan else 1) != len(row[3])
                                   for (_, plan), row in zip(groups, rows)):
            raise PlanRejected('PLANNER_BRANCHES_DEPENDENT')
        subplans = []
        for (question, plan), (_, entity, time_text, steps) in zip(groups, rows):
            _check_grounding(question, entity, time_text, steps, scope)
            if plan is not None:  # a single step has no TaskPlan shape; the combined check covered it
                validate_model_proposed_plan(plan.to_dict(), user_query=question, max_steps=scope.max_steps)
            turns = 2 * len(steps) + 1
            if turns > scope.max_requests or turns > scope.max_output_tokens or turns > scope.max_steps:
                raise PlanRejected('PLANNER_BUDGET_INSUFFICIENT')
            subplans.append((question, plan))
    except PlanRejected:
        raise
    except Exception:
        raise PlanRejected('PLANNER_PLAN_INVALID') from None
    return tuple(subplans)


def plan_branches(adapter, scope):
    """One bound complete() call, then Host validation. adapter is session.bind(..., 'plan')."""
    return validate_branch_plan(_tool_arguments(adapter.complete(branch_plan_request(scope.query))), scope)
