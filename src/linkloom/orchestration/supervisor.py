"""Supervisor: an LLM tool call proposes delegation, the Host validates every field.

The model never grants a tool, a source, a date, a budget or a permission. It proposes tasks with
dependencies; accept_delegation applies the Host limits and returns the tasks to add or raises
DelegationRejected with a stable reason code. The Host decides what happens next on rejection:
the first plan falls back to one task for the original question, a review stops and synthesizes.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
from linkloom.orchestration.state import ACCEPTED, PENDING
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.tools.contracts import ToolDefinition

TOOL_ID = 'submit_delegation'
VERSION = 'delegation/v1'
SUPERVISOR_OUTPUT_TOKENS = 4096
ANSWER_SINGLE, DELEGATE, STOP = 'answer_single', 'delegate', 'stop'
SINGLE_TASK_ID = 'task-1'
_ID = re.compile(r'[A-Za-z0-9_-]{1,40}')
_TRUNCATED = frozenset({'length', 'max_tokens'})
_TASK = dict(type='object', properties=dict(id=dict(type='string'), description=dict(type='string'),
    depends_on=dict(type='array', items=dict(type='string')), spawn_reason=dict(type='string')),
    required=['id', 'description', 'depends_on', 'spawn_reason'])
_SCHEMA = dict(type='object', properties=dict(
    action=dict(type='string', enum=[ANSWER_SINGLE, DELEGATE, STOP]), reason=dict(type='string'),
    tasks=dict(type='array', items=_TASK)), required=['action', 'reason', 'tasks'])
_PROMPT = (
    "You are the Supervisor of a read-only investigation over a verified workspace. Decide how to answer the "
    "user_goal. Workers search and read sources; you never see source text unless a Worker verified it.\n"
    "Context fields: confirmed_scope is what the Host authorized and confirmed. entity_leads are names from the "
    "question that the Host could not confirm; they are leads, not facts. Do not assume a lead is in scope. "
    "Write a task that asks a Worker to resolve a lead through source evidence when the answer depends on it.\n"
    "Actions: answer_single = one Worker can independently complete the investigation within the supplied "
    "Worker turn, input/output, cost and time limits. A single user question can contain separable objectives; "
    "its sentence count is not a reason to choose one Worker. "
    "worker_max_turns is only a hard ceiling INCLUDING Final, never that many evidence-gathering turns. "
    "worker_planning_research_turns is the conditional space after reserving Final, assuming each research "
    "request uses at most worker_planning_input_per_turn and a full request timeout. Larger histories or "
    "failures can force Final earlier; no research-turn count is guaranteed. Plan within that conditional "
    "space rather than the hard ceiling, and allow explicit partial results if it cannot cover the goal. "
    "Batch independent searches or known evidence reads in one turn using existing multi-tool calls. "
    "A downstream entity-dependent search must follow its verified evidence; batching does not remove "
    "that dependency. Separate focused tasks may reuse reviewed upstream results via depends_on. "
    "Unused slices are not pooled into a single Worker. All tasks share run_seconds, including Supervisor "
    "overhead: dependent tasks cannot each spend worker_seconds, and their latencies add along the chain. "
    "delegate = create self-contained tasks for "
    "separable objectives. Use depends_on when a task needs an upstream verified entity or relationship; "
    "independent tasks may run in parallel. Reuse the Blackboard's reviewed results and "
    "verified_upstream_results instead of repeating completed work. Never reuse failed or unreviewed facts. "
    "stop = enough has been established or more work "
    "cannot help; only valid when tasks already exist. In a review, delegate adds follow-up tasks for the "
    "specific remaining gaps, rather than repeating the entire failed investigation, and may depend on "
    "earlier accepted tasks. Limits: at most {max_tasks} tasks in total, depth at most "
    "{max_depth}. Reply only by calling submit_delegation.\ncontext=")


class DelegationRejected(ValueError):
    """The proposal is unusable; the message is a stable reason code."""


def delegation_request(query, context, decision_key):
    stamp = canonical_hash([query, decision_key])[:24]
    schema = deepcopy(_SCHEMA)
    prompt = _PROMPT.format(max_tasks=context['limits']['max_tasks'], max_depth=context['limits']['max_depth'])
    return ModelTurnRequest(run_id='delegation_' + stamp, turn_id='delegationturn_' + stamp,
        task_id='delegationtask_' + stamp, agent_id='orchestration-supervisor', sequence=1,
        user_input=prompt + json.dumps(context, ensure_ascii=False), observation=None,
        available_tools=[ToolDefinition(tool_id=TOOL_ID, version=VERSION,
            description='Propose delegation, a single answer, or stop.', input_schema=schema,
            output_schema=deepcopy(schema))],
        generation_options=ModelGenerationOptions(max_output_tokens=SUPERVISOR_OUTPUT_TOKENS,
                                                  require_tool_call=True))


def tool_arguments(response):
    if response is None or response.error is not None:
        raise DelegationRejected('SUPERVISOR_PROVIDER_ERROR')
    if str(response.finish_reason or '').lower() in _TRUNCATED:
        raise DelegationRejected('SUPERVISOR_TRUNCATED')
    if response.proposal is not None:
        calls = [item.runtime_call for item in response.proposal.tool_calls]
    elif response.action is not None and response.action.kind == 'tool_call':
        calls = [response.action.tool_call]
    else:
        calls = []
    if len(calls) != 1 or calls[0].tool_id != TOOL_ID:
        raise DelegationRejected('SUPERVISOR_NO_TOOL_CALL')
    return calls[0].arguments


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DelegationRejected('SUPERVISOR_SCHEMA_INVALID')
    return value.strip()


def _parse(payload):
    if not isinstance(payload, dict) or set(payload) - {'action', 'reason', 'tasks'}:
        raise DelegationRejected('SUPERVISOR_SCHEMA_INVALID')
    action = payload.get('action')
    if action not in (ANSWER_SINGLE, DELEGATE, STOP) or not isinstance(payload.get('tasks', []), list):
        raise DelegationRejected('SUPERVISOR_SCHEMA_INVALID')
    tasks = []
    for item in payload.get('tasks', []):
        if not isinstance(item, dict) or set(item) != {'id', 'description', 'depends_on', 'spawn_reason'} \
                or not isinstance(item['depends_on'], list):
            raise DelegationRejected('SUPERVISOR_SCHEMA_INVALID')
        task_id = _text(item['id'], 40)
        if not _ID.fullmatch(task_id):
            raise DelegationRejected('SUPERVISOR_TASK_ID_INVALID')
        tasks.append(dict(id=task_id, description=_text(item['description'], 1000),
                          depends_on=[_text(dep, 40) for dep in item['depends_on']],
                          spawn_reason=_text(item['spawn_reason'], 300)))
    return action, str(payload.get('reason') or '')[:300], tasks


def _depths(tasks):
    """Depth of every task (a root is 1). Raises on a cycle."""
    depth, visiting = {}, set()

    def visit(task_id):
        if task_id in depth:
            return depth[task_id]
        if task_id in visiting:
            raise DelegationRejected('SUPERVISOR_DEPENDENCY_CYCLE')
        visiting.add(task_id)
        depth[task_id] = 1 + max((visit(dep) for dep in tasks[task_id]['depends_on']), default=0)
        visiting.discard(task_id)
        return depth[task_id]
    for task_id in tasks:
        visit(task_id)
    return depth


def make_task(proposed, *, depth, round_number, reason=None):
    return dict(id=proposed['id'], description=proposed['description'], depends_on=list(proposed['depends_on']),
                status=PENDING, result_ref=None, spawn_reason=proposed['spawn_reason'] if reason is None else reason,
                depth=depth, attempt=0, epoch=0, round=round_number, failure=None)


def single_task(query, reason):
    return {SINGLE_TASK_ID: make_task(dict(id=SINGLE_TASK_ID, description=query.strip(), depends_on=[]),
                                      depth=1, round_number=0, reason=reason)}


def accept_delegation(payload, *, query, existing, limits, blocked=(), round_number):
    """Return (action, reason, new_tasks). Raises DelegationRejected. existing is the current task map."""
    from linkloom.ui.router_context import blocked_entity_reason
    action, reason, proposed = _parse(payload)
    first = not existing
    if action == STOP and first:
        raise DelegationRejected('SUPERVISOR_STOP_WITHOUT_TASKS')
    if action == ANSWER_SINGLE:
        if proposed or not first:
            raise DelegationRejected('SUPERVISOR_SINGLE_NOT_ALLOWED')
        return action, reason, single_task(query, 'SUPERVISOR_ANSWER_SINGLE')
    if action == STOP:
        if proposed:
            raise DelegationRejected('SUPERVISOR_STOP_WITH_TASKS')
        return action, reason, {}
    if not proposed:
        raise DelegationRejected('SUPERVISOR_NO_TASKS')
    known = {item['id'] for item in proposed}
    if len(known) != len(proposed) or known & set(existing):
        raise DelegationRejected('SUPERVISOR_TASK_ID_DUPLICATE')
    room = limits.task_cap() - len(existing)
    if len(proposed) > room:
        raise DelegationRejected('SUPERVISOR_TASK_LIMIT')
    graph = {task_id: dict(task) for task_id, task in existing.items()}
    for item in proposed:
        for dep in item['depends_on']:
            if dep == item['id'] or (dep not in known and dep not in existing):
                raise DelegationRejected('SUPERVISOR_DEPENDENCY_UNKNOWN')
            if dep in existing and existing[dep]['status'] != ACCEPTED:
                raise DelegationRejected('SUPERVISOR_DEPENDENCY_NOT_VERIFIED')
        if blocked_entity_reason(item['description'], tuple(map(tuple, blocked))):
            raise DelegationRejected('SUPERVISOR_ENTITY_BLOCKED')
        graph[item['id']] = dict(item)
    depth = _depths(graph)
    if max(depth[item['id']] for item in proposed) > limits.max_depth:
        raise DelegationRejected('SUPERVISOR_DEPTH_LIMIT')
    return action, reason, {item['id']: make_task(item, depth=depth[item['id']], round_number=round_number)
                            for item in proposed}
