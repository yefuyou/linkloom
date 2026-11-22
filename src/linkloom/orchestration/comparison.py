"""One bounded claim comparison for the final synthesis. No new Agent: one tool-call request at the Provider seam.

Structure decides first (branch_synthesis.plan_comparison). Only pairs of answers that structure cannot decide
are sent, all in one request, and only when at least one such pair exists. A run with one answer, or whose pairs
are all decided by structure, makes no call.

What the model may return is a verdict per requested pair and nothing else. The Host checks the reply against
the pairs it asked for (known task ids, each pair exactly once, an allowed verdict, no other field). The reply
carries no text, so it cannot add a fact or a citation, and the conflict sides are built later from the answers
the Reviewer already passed.

Recovery: result.json is the receipt. Once it exists (accepted verdicts or a failure) the request is never sent
again; a budget journal with requests but no receipt means a call may have been in flight, which is reported
as UNAVAILABLE (PROVIDER_UNCERTAIN) and is not repeated. The request runs under the run's Provider hard limits
and records its own budget.json like every other session.
"""
from __future__ import annotations

from copy import deepcopy
import json

from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
from linkloom.orchestration.files import read_json, write_json
from linkloom.orchestration.worker import close_session, paid_without_receipt
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.tools.contracts import ToolDefinition
from linkloom.ui.branch_synthesis import UNCERTAIN, VERDICTS
from linkloom.ui.task_provider import TaskProviderSession

TOOL_ID = 'submit_comparison'
VERSION = 'comparison/v1'
AGENT_ID = 'orchestration-comparator'
MAX_PAIRS = 15  # every pair of six answers; pairs beyond this are not sent and stay UNCERTAIN
OUTPUT_TOKENS = 1024
_TRUNCATED = frozenset({'length', 'max_tokens'})
_PAIR = dict(type='object', properties=dict(a=dict(type='string'), b=dict(type='string'),
             verdict=dict(type='string', enum=list(VERDICTS))), required=['a', 'b', 'verdict'])
_SCHEMA = dict(type='object', properties=dict(pairs=dict(type='array', items=_PAIR)), required=['pairs'])
_PROMPT = (
    "Compare answers that different tasks of one investigation gave. Each pair is (a, b). Judge only from the "
    "question and answer shown; add no fact. Verdicts: AGREE = both answers state the same thing about the same "
    "matter. CONFLICT = they ask about the same matter and the answers cannot both be true. UNRELATED = they "
    "are about different matters. UNCERTAIN = you cannot tell. Give exactly one verdict for every requested "
    "pair, using the given task ids. Reply only by calling submit_comparison.\ncontext=")


class ComparisonRejected(ValueError):
    """The reply is unusable; the message is a stable reason code."""


def comparison_request(claims, pairs, stamp):
    schema = deepcopy(_SCHEMA)
    context = dict(claims=[dict(id=c['branch'], question=c['question'], answer=c['value']) for c in claims],
                   pairs=[dict(a=a, b=b) for a, b in pairs])
    return ModelTurnRequest(run_id='comparison_' + stamp, turn_id='comparisonturn_' + stamp,
        task_id='comparisontask_' + stamp, agent_id=AGENT_ID, sequence=1,
        user_input=_PROMPT + json.dumps(context, ensure_ascii=False), observation=None,
        available_tools=[ToolDefinition(tool_id=TOOL_ID, version=VERSION,
            description='Give one verdict per requested pair.', input_schema=schema,
            output_schema=deepcopy(schema))],
        generation_options=ModelGenerationOptions(max_output_tokens=OUTPUT_TOKENS, require_tool_call=True))


def tool_arguments(response):
    if response is None or response.error is not None:
        raise ComparisonRejected('COMPARISON_PROVIDER_ERROR')
    if str(response.finish_reason or '').lower() in _TRUNCATED:
        raise ComparisonRejected('COMPARISON_TRUNCATED')
    if response.proposal is not None:
        calls = [item.runtime_call for item in response.proposal.tool_calls]
    elif response.action is not None and response.action.kind == 'tool_call':
        calls = [response.action.tool_call]
    else:
        calls = []
    if len(calls) != 1 or calls[0].tool_id != TOOL_ID:
        raise ComparisonRejected('COMPARISON_NO_TOOL_CALL')
    return calls[0].arguments


def validate_verdicts(payload, pairs, known):
    """Host schema check. Returns {(a, b): verdict} for exactly the requested pairs, else raises."""
    if not isinstance(payload, dict) or set(payload) != {'pairs'} or not isinstance(payload['pairs'], list):
        raise ComparisonRejected('COMPARISON_SCHEMA_INVALID')
    wanted, found = {frozenset(pair): pair for pair in pairs}, {}
    for item in payload['pairs']:
        if not isinstance(item, dict) or set(item) != {'a', 'b', 'verdict'}:
            raise ComparisonRejected('COMPARISON_SCHEMA_INVALID')
        if item['a'] not in known or item['b'] not in known:
            raise ComparisonRejected('COMPARISON_UNKNOWN_TASK')
        key = frozenset((item['a'], item['b']))
        if key not in wanted or wanted[key] in found or item['verdict'] not in VERDICTS:
            raise ComparisonRejected('COMPARISON_SCHEMA_INVALID')
        found[wanted[key]] = item['verdict']
    if len(found) != len(wanted):
        raise ComparisonRejected('COMPARISON_SCHEMA_INVALID')
    return found


def compare_pending(deps, plan):
    """Return ({(a, b): (verdict, basis)}, reason). reason is None when every sent pair got a verdict.

    Idempotent: the receipt makes a replay (resume after a restart) a read, never a second request."""
    pending = list(plan['pending'])
    if not pending:
        return {}, None
    sent, overflow = pending[:MAX_PAIRS], pending[MAX_PAIRS:]
    extra = {pair: (UNCERTAIN, 'COMPARISON_LIMIT') for pair in overflow}
    root = deps.root / 'comparison'
    stamp = canonical_hash([deps.run_id, sent])[:24]
    saved = read_json(root / 'result.json')
    if isinstance(saved, dict):
        if saved.get('stamp') != stamp:
            return extra, 'COMPARISON_RECEIPT_STALE'
        return _from_receipt(saved, sent, extra)
    if paid_without_receipt(root):  # a request may have been in flight: never send it again
        saved = dict(stamp=stamp, verdicts=None, reason='PROVIDER_UNCERTAIN')
        write_json(root / 'result.json', saved)
        return _from_receipt(saved, sent, extra)
    limits = deps.limits.provider
    known = {claim['branch'] for claim in plan['claims']}
    claims = [claim for claim in plan['claims'] if any(claim['branch'] in pair for pair in sent)]
    verdicts, reason = None, None
    try:
        session = TaskProviderSession(root, limits, deps.validate)
    except Exception:
        return extra, 'COMPARISON_FAILED'
    try:
        request = comparison_request(claims, sent, stamp)
        response = session.bind(deps.model_factory(stamp), 'comparison').complete(request)
        found = validate_verdicts(tool_arguments(response), sent, known)
        verdicts = [dict(a=a, b=b, verdict=found[(a, b)]) for a, b in sent]
    except ComparisonRejected as exc:
        reason = str(exc)
    except Exception:
        reason = 'COMPARISON_FAILED'
    finally:
        close_session(session)
    saved = dict(stamp=stamp, verdicts=verdicts, reason=session.failure or reason)
    if session.failure:
        saved['verdicts'] = None
    write_json(root / 'result.json', saved)
    return _from_receipt(saved, sent, extra)


def _from_receipt(saved, sent, extra):
    if not saved.get('verdicts'):
        return extra, saved.get('reason') or 'COMPARISON_FAILED'
    found = {(item['a'], item['b']): (item['verdict'], 'semantic_comparison') for item in saved['verdicts']}
    if not all(pair in found for pair in sent):
        return extra, 'COMPARISON_RECEIPT_STALE'
    return {**found, **extra}, None
