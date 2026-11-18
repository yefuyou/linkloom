"""Deterministic synthesis of verified branch conclusions. No model call is made here.

Each reviewed branch already passed its own Reviewer in the Runtime. This module only composes
what those branches reported, then checks the composition once more with the existing
ReviewerAgent shell and two injected aggregate checks.

Limits, stated plainly:
* The aggregate checks reject forged or stale citations (identity not carried by a passed branch,
  quote text that does not match its hash, a changed source, a citation outside the merged set,
  an unverified operand branch, forbidden fields). They cannot judge whether a quote supports a
  conclusion. That judgement stays with the per-branch Reviewer.
* Whether two answers conflict is decided in this order. (1) A trusted structured identity
  conclusion['claim_key'] (subject, relation, optional as_of) set by the Runtime: different identities
  are unrelated. (2) Identical answer text cannot contradict itself: AGREE. (3) A task that depends on
  the other is a later step, not a competing claim: UNRELATED. (4) Everything else is a pending pair.
  A pending pair needs a meaning judgement, which this module never makes. The Host asks one bounded
  comparison (orchestration/comparison.py) and passes the validated verdicts in as `comparison`.
  Without them a pending pair is UNAVAILABLE, never silently "no conflict".
* A verdict is only AGREE, CONFLICT, UNRELATED, UNCERTAIN or UNAVAILABLE. The comparison adds no fact,
  no citation and no evidence: conflict sides are built here from the Reviewer-passed answers.
  No winner is chosen.
* result['conflict_check']['status'] is separate from result['status']. COMPLETE or PARTIAL describes
  answer completeness only; a real CONFLICT makes it PARTIAL. UNAVAILABLE or UNCERTAIN leaves it unchanged and
  is reported as verified=False, so a reader is told the conflict check did not run or was inconclusive.
"""

from datetime import datetime, timezone
import hashlib

from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.tools.tool_policy import P4_TOOL_POLICY_VERSION, ToolCallPolicy, ToolPolicyEnforcer

FORBIDDEN_FIELDS = frozenset({'gold', 'expected', 'label', 'mutation_approval'})
_NO_DECISION = ('insufficient_evidence', 'not_found')
AGREE, CONFLICT, UNRELATED, UNCERTAIN, UNAVAILABLE = 'AGREE', 'CONFLICT', 'UNRELATED', 'UNCERTAIN', 'UNAVAILABLE'
NOT_APPLICABLE = 'NOT_APPLICABLE'  # fewer than two answers: there is nothing to compare
VERDICTS = (AGREE, CONFLICT, UNRELATED, UNCERTAIN)  # what a comparison may return; UNAVAILABLE is Host-only
_SEVERITY = (CONFLICT, UNAVAILABLE, UNCERTAIN, AGREE, UNRELATED)  # overall status: first present wins
_VERIFIED = frozenset({AGREE, CONFLICT, UNRELATED})


def identity_of(item):
    return (item['source_id'], item['source_version'], item['line_start'], item['line_end'],
            item['quote_sha256'])


def merge_evidence(rows):
    """Merge branch evidence into one index keyed by identity, numbered citation-N in first-seen order."""
    index = {}
    for row in rows:
        for item in row['evidence']:
            index.setdefault(identity_of(item), dict(item))
    return {key: dict(item, id=f'citation-{i}', ordinal=i) for i, (key, item) in enumerate(index.items(), 1)}


def _quote(item):
    source = item.get('source')
    return source.get('quote') if isinstance(source, dict) else item.get('quote')


def _name(row):
    return row['run']['branch']


def _unknown(reason, question=None, branch=None, detail=None):
    entry = dict(question=question, branch=branch, reason=reason)
    if detail is not None:
        entry['detail'] = detail
    return entry


def _status(answers, unknowns, conflicts, rows_count):
    if not answers:
        return 'UNKNOWN'
    return 'COMPLETE' if len(answers) == rows_count and not unknowns and not conflicts else 'PARTIAL'


def _identity(key):
    return (key['subject'], key['relation'], key.get('as_of')) if key else None


def _claims(rows):
    """Answers that take part in a comparison: a decision value, in branch-name order."""
    claims = []
    for row in sorted(rows, key=_name):
        conclusion = row.get('conclusion') or {}
        decision = conclusion.get('decision') or {}
        if decision.get('value') and decision.get('status') not in _NO_DECISION:
            claims.append(dict(branch=_name(row), question=conclusion.get('question'),
                               value=conclusion.get('claim_value') or decision['value'],
                               identity=_identity(conclusion.get('claim_key'))))
    return claims


def _closure(dependencies):
    reach = {}

    def visit(node, seen=()):
        if node not in reach:
            found = set()
            for dep in (dependencies or {}).get(node, ()):
                if dep not in seen:
                    found.add(dep)
                    found |= visit(dep, seen + (node,))
            reach[node] = found
        return reach[node]
    for node in (dependencies or {}):
        visit(node)
    return reach


def plan_comparison(rows, dependencies=None):
    """Pure. Split every pair of answers into decided (structure or identical text) and pending (needs meaning).

    dependencies maps a task id to the task ids it depends on (Host-held, never model output)."""
    claims = _claims(rows)
    reach = _closure(dependencies)
    decided, pending = [], []
    for i, first in enumerate(claims):
        for second in claims[i + 1:]:
            a, b = first['branch'], second['branch']
            if first['identity'] and second['identity'] and first['identity'] != second['identity']:
                decided.append(dict(a=a, b=b, verdict=UNRELATED, basis='structured_identity'))
            elif first['value'] == second['value']:
                decided.append(dict(a=a, b=b, verdict=AGREE, basis='identical_value'))
            elif b in reach.get(a, ()) or a in reach.get(b, ()):
                decided.append(dict(a=a, b=b, verdict=UNRELATED, basis='dependent_task'))
            else:
                pending.append((a, b))
    return dict(claims=claims, decided=decided, pending=pending)


def resolve_pairs(plan, verdicts=None, reason='COMPARISON_NOT_RUN'):
    """Decided pairs plus one verdict per pending pair. A pending pair without a verdict is UNAVAILABLE."""
    pairs = list(plan['decided'])
    for pair in plan['pending']:
        found = (verdicts or {}).get(pair)
        pairs.append(dict(a=pair[0], b=pair[1], verdict=found[0] if found else UNAVAILABLE,
                          basis=found[1] if found else reason))
    return sorted(pairs, key=lambda p: (p['a'], p['b']))


def _conflicts(answers, pairs):
    by_branch = {a['branch']: a for a in answers}
    conflicts = []
    for pair in pairs:
        if pair['verdict'] != CONFLICT:
            continue
        sides = [dict(branch=a['branch'], question=a['question'], value=a['claim_value'] or a['text'],
                      citations=list(a['citations'])) for a in (by_branch[pair['a']], by_branch[pair['b']])]
        keys = {_identity(a['claim_key']) for a in (by_branch[pair['a']], by_branch[pair['b']])}
        key = next(iter(keys)) if len(keys) == 1 else None
        conflicts.append(dict(subject=key[0] if key else None, relation=key[1] if key else None,
                              basis=pair['basis'], sides=sides))
    return conflicts


def _conflict_check(answers, pairs):
    statuses = {pair['verdict'] for pair in pairs}
    status = NOT_APPLICABLE if len(answers) < 2 else next((s for s in _SEVERITY if s in statuses), UNRELATED)
    compared = {branch for pair in pairs if pair['verdict'] in (AGREE, CONFLICT) for branch in (pair['a'], pair['b'])}
    reasons = sorted({pair['basis'] for pair in pairs if pair['verdict'] in (UNAVAILABLE, UNCERTAIN)})
    return dict(status=status, verified=status in _VERIFIED or status == NOT_APPLICABLE,
                reasons=reasons, pairs=pairs, cross_branch_compared=bool(compared), compared=sorted(compared),
                uncompared=sorted(a['branch'] for a in answers if a['branch'] not in compared))


def _compose(rows, gaps, unstarted, evidence_index, questions, comparison=None):
    # questions is a list in branch-N order (two-branch caller) or a {task id: question} mapping (N tasks).
    named = dict(questions) if isinstance(questions, dict) else {}
    questions = [] if named else list(questions or ())
    by_name = {_name(row): row for row in rows}
    cited = {identity: item['id'] for identity, item in evidence_index.items()}
    answers, unknowns = [], []
    for name in sorted(by_name):
        conclusion = by_name[name].get('conclusion') or {}
        question = conclusion.get('question')
        decision = conclusion.get('decision') or {}
        if not decision.get('value') or decision.get('status') in _NO_DECISION:
            unknowns.append(_unknown('BRANCH_NO_DECISION', question, name))
        else:
            citations = [cited[identity_of(item)] for item in conclusion.get('evidence', ())
                         if identity_of(item) in cited]
            answers.append(dict(branch=name, question=question, text=decision['value'],
                                citations=sorted(set(citations), key=lambda c: int(c.split('-')[1])),
                                claim_key=conclusion.get('claim_key'),
                                claim_value=conclusion.get('claim_value')))
            if decision.get('status') == 'partial':
                unknowns.append(_unknown('BRANCH_DECISION_PARTIAL', question, name))
        for item in conclusion.get('unresolved_items', ()):
            unknowns.append(_unknown('BRANCH_UNRESOLVED_ITEM', question, name, item))
        uncertainty = conclusion.get('uncertainty') or {}
        if uncertainty.get('status') == 'conflicting_evidence':
            unknowns.append(_unknown('BRANCH_REPORTS_CONFLICTING_EVIDENCE', question, name))
    for name in sorted(gaps):
        number = int(name.rsplit('-', 1)[-1]) if name.rsplit('-', 1)[-1].isdigit() else 0
        question = named.get(name) or (questions[number - 1] if 0 < number <= len(questions) else None)
        unknowns.append(_unknown('BRANCH_' + str(gaps[name]), question, name))
    pending = [f'branch-{i}' for i in range(1, len(questions) + 1)
               if f'branch-{i}' not in by_name and f'branch-{i}' not in gaps]
    for i in range(unstarted):
        name = pending[i] if i < len(pending) else None
        unknowns.append(_unknown('BRANCH_UNSTARTED', questions[int(name[7:]) - 1] if name else None, name))
    pairs = comparison if comparison is not None else resolve_pairs(plan_comparison(rows))
    conflicts = _conflicts(answers, pairs)
    return dict(
        status=_status(answers, unknowns, conflicts, len(rows) + len(gaps) + unstarted),
        answers=answers, conflicts=conflicts, unknowns=unknowns, conflict_check=_conflict_check(answers, pairs))


def _text(draft):
    lines = []
    for a in draft['answers']:
        lines.append(f"{a['question']}: {a['text']} [{', '.join(a['citations'])}]")
    for c in draft['conflicts']:
        topic = f"{c['subject']} / {c['relation']}" if c['subject'] else 'the same question'
        lines.append(f"Conflict on {topic}: " + '; '.join(
            f"{s['branch']} says {s['value']} [{', '.join(s['citations'])}]" for s in c['sides']))
    check = draft['conflict_check']
    if check['status'] in (UNAVAILABLE, UNCERTAIN):
        lines.append(f"Conflict check {check['status']}: answers were not verified against each other")
    for u in draft['unknowns']:
        lines.append(f"Unknown: {u['question'] or u['branch'] or 'unassigned question'} ({u['reason']})")
    return '\n'.join(lines)


def synthesize(rows, gaps, unstarted, evidence_index, questions=None, comparison=None):
    """Pure and deterministic: the same input gives an equal result. comparison is resolve_pairs output."""
    draft = _compose(rows, gaps, unstarted, evidence_index, questions, comparison)
    draft['text'] = _text(draft)
    return draft


def _forbidden(value):
    if isinstance(value, dict):
        return any(str(k).casefold() in FORBIDDEN_FIELDS or _forbidden(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_forbidden(v) for v in value)
    return False


def validate_aggregate_evidence(rows, evidence_index, source_check=None):
    """Build the validate_ev_func for ReviewerAgent. Takes 'citation-N', returns status and reason_code."""
    by_id = {item['id']: (identity, item) for identity, item in evidence_index.items()}
    carried = {identity_of(item) for row in rows for item in row['evidence']}

    def check(ref):
        if ref not in by_id or by_id[ref][0] not in carried:
            return dict(status='fail', reason_code='AGGREGATE_EVIDENCE_UNKNOWN')
        identity, item = by_id[ref]
        quote = _quote(item)
        if not isinstance(quote, str) or hashlib.sha256(quote.encode('utf-8')).hexdigest() != identity[4]:
            return dict(status='fail', reason_code='AGGREGATE_QUOTE_HASH_MISMATCH')
        try:
            if source_check is not None:
                source_check()
        except Exception:
            return dict(status='fail', reason_code='AGGREGATE_SOURCE_CHANGED')
        return dict(status='pass', reason_code=None)
    return check


def validate_aggregate_schema(rows, evidence_index):
    """Build the validate_sch_func for ReviewerAgent."""
    merged = {item['id'] for item in evidence_index.values()}
    verified = {_name(row) for row in rows}

    def check(_result_type, payload):
        if _forbidden(payload):
            return dict(status='fail', reason_code='AGGREGATE_FIELD_FORBIDDEN')
        if payload.get('branch') not in verified:
            return dict(status='fail', reason_code='AGGREGATE_BRANCH_UNVERIFIED')
        citations = payload.get('citations')
        if not citations or any(c not in merged for c in citations):
            return dict(status='fail', reason_code='AGGREGATE_CITATION_MISSING')
        return dict(status='pass', reason_code=None)
    return check


def _review(draft, rows, evidence_index, source_check, review_id):
    refs = sorted({c for a in draft['answers'] for c in a['citations']})
    payloads = [{k: v for k, v in a.items() if k not in ('claim_key', 'claim_value')}
                for a in draft['answers']]
    if any(_forbidden(p) for p in payloads):  # the shared schema tool raises on top-level keys; keep the code
        return 'AGGREGATE_FIELD_FORBIDDEN'
    policy = ToolCallPolicy(P4_TOOL_POLICY_VERSION, 'branch_synthesizer', ['validate_evidence', 'validate_schema'],
                            [], len(refs) + len(payloads) + 1, 'deny', 'deny', 'deny')
    decision = ReviewerAgent('branch_synthesizer_review').execute(
        review_id, sorted(a['branch'] for a in draft['answers']), payloads, refs, ToolPolicyEnforcer(policy),
        validate_aggregate_evidence(rows, evidence_index, source_check),
        validate_aggregate_schema(rows, evidence_index))
    if decision.decision == 'evidence_sufficient':
        return None
    failed = [c.get('reason_code') for c in decision.checks if c.get('status') != 'pass']
    return failed[0] if failed and failed[0] else 'AGGREGATE_REVIEW_ERROR'


def _fallback(status, draft, evidence_index, reason):
    """Evidence and unknowns only. Answers whose review failed become unknowns; nothing is published."""
    unknowns = list(draft['unknowns']) if draft else []
    for a in (draft['answers'] if draft else ()):
        unknowns.append(_unknown(reason, a['question'], a['branch']))
    result = dict(status=status, answers=[], conflicts=[], unknowns=unknowns, published=False,
                  fallback_reason=reason, conflict_check=draft['conflict_check'] if draft else None,
                  evidence=[{k: v for k, v in item.items() if k in ('id', 'source_id', 'source_version',
                             'line_start', 'line_end', 'quote_sha256')} for item in evidence_index.values()])
    result['text'] = '\n'.join(f"Unknown: {u['question'] or u['branch'] or 'unassigned question'} ({u['reason']})"
                               for u in unknowns)
    return result


def synthesize_reviewed(rows, gaps, unstarted, evidence_index, questions=None, source_check=None,
                        review_id='aggregate-review', comparison=None):
    """Synthesize, review the aggregate, and fall back instead of ever raising."""
    draft = None
    try:
        draft = synthesize(rows, gaps, unstarted, evidence_index, questions, comparison)
        reason = _review(draft, rows, evidence_index, source_check, review_id) if draft['answers'] else None
    except Exception:
        reason = 'AGGREGATE_SYNTHESIS_FAILED'
    try:
        if reason is None:
            return dict(draft, published=True, fallback_reason=None,
                        reviewed_at=datetime.now(timezone.utc).isoformat())
        return _fallback(draft['status'] if draft else 'UNKNOWN', draft, evidence_index, reason)
    except Exception:
        return dict(status='UNKNOWN', answers=[], conflicts=[], unknowns=[], evidence=[], text='',
                    published=False, fallback_reason='AGGREGATE_SYNTHESIS_FAILED')
