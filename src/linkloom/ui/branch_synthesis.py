"""Deterministic synthesis of verified branch conclusions. No model call is made here.

Each reviewed branch already passed its own Reviewer in the Runtime. This module only composes
what those branches reported, then checks the composition once more with the existing
ReviewerAgent shell and two injected aggregate checks.

Limits, stated plainly:
* The aggregate checks reject forged or stale citations (identity not carried by a passed branch,
  quote text that does not match its hash, a changed source, a citation outside the merged set,
  an unverified operand branch, forbidden fields). They cannot judge whether a quote supports a
  conclusion. That judgement stays with the per-branch Reviewer.
* Conflicts are decided from structured fields only (conclusion['claim_key'] plus
  conclusion['claim_value']). Natural language is never compared and no winner is chosen.
  The branch artifact currently carries no structured subject/relation, so branches without a
  claim_key are not compared; they are listed under result['conflict_check']['uncompared']
  instead of being claimed as conflict free.
"""

from datetime import datetime, timezone
import hashlib

from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.tools.tool_policy import P4_TOOL_POLICY_VERSION, ToolCallPolicy, ToolPolicyEnforcer

FORBIDDEN_FIELDS = frozenset({'gold', 'expected', 'label', 'mutation_approval'})
_NO_DECISION = ('insufficient_evidence', 'not_found')


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


def _sides(answers):
    groups = {}
    for answer in answers:
        key = answer.get('claim_key')
        if key:
            groups.setdefault((key['subject'], key['relation']), []).append(answer)
    conflicts = []
    for (subject, relation), group in sorted(groups.items()):
        if len({answer['claim_value'] for answer in group}) > 1:
            conflicts.append(dict(subject=subject, relation=relation, sides=[
                dict(branch=a['branch'], value=a['claim_value'], citations=list(a['citations']))
                for a in sorted(group, key=lambda a: a['branch'])]))
    return conflicts


def _compose(rows, gaps, unstarted, evidence_index, questions):
    questions = list(questions or ())
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
        question = questions[number - 1] if 0 < number <= len(questions) else None
        unknowns.append(_unknown('BRANCH_' + str(gaps[name]), question, name))
    pending = [f'branch-{i}' for i in range(1, len(questions) + 1)
               if f'branch-{i}' not in by_name and f'branch-{i}' not in gaps]
    for i in range(unstarted):
        name = pending[i] if i < len(pending) else None
        unknowns.append(_unknown('BRANCH_UNSTARTED', questions[int(name[7:]) - 1] if name else None, name))
    conflicts = _sides(answers)
    compared = {a['branch'] for a in answers if a.get('claim_key')}
    return dict(
        status=_status(answers, unknowns, conflicts, len(rows) + len(gaps) + unstarted),
        answers=answers, conflicts=conflicts, unknowns=unknowns,
        conflict_check=dict(compared=sorted(compared),
                            uncompared=sorted(a['branch'] for a in answers if a['branch'] not in compared)))


def _text(draft):
    lines = []
    for a in draft['answers']:
        lines.append(f"{a['question']}: {a['text']} [{', '.join(a['citations'])}]")
    for c in draft['conflicts']:
        lines.append(f"Conflict on {c['subject']} / {c['relation']}: " + '; '.join(
            f"{s['branch']} says {s['value']} [{', '.join(s['citations'])}]" for s in c['sides']))
    for u in draft['unknowns']:
        lines.append(f"Unknown: {u['question'] or u['branch'] or 'unassigned question'} ({u['reason']})")
    return '\n'.join(lines)


def synthesize(rows, gaps, unstarted, evidence_index, questions=None):
    """Pure and deterministic: the same input gives an equal result."""
    draft = _compose(rows, gaps, unstarted, evidence_index, questions)
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
                  fallback_reason=reason,
                  evidence=[{k: v for k, v in item.items() if k in ('id', 'source_id', 'source_version',
                             'line_start', 'line_end', 'quote_sha256')} for item in evidence_index.values()])
    result['text'] = '\n'.join(f"Unknown: {u['question'] or u['branch'] or 'unassigned question'} ({u['reason']})"
                               for u in unknowns)
    return result


def synthesize_reviewed(rows, gaps, unstarted, evidence_index, questions=None, source_check=None,
                        review_id='aggregate-review'):
    """Synthesize, review the aggregate, and fall back instead of ever raising."""
    draft = None
    try:
        draft = synthesize(rows, gaps, unstarted, evidence_index, questions)
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
