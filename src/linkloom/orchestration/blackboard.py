"""Evidence Blackboard entries and the structured input a Worker receives.

Workers share evidence (identity, version, hashes, owner task), never their reasoning. Only a result that
passed the Reviewer gate contributes verified evidence; an Agent's conclusion is recorded as an inference.
"""
from __future__ import annotations

from linkloom.ui.branch_synthesis import identity_of

_QUOTE_LIMIT = 400
_EVIDENCE_PER_TASK = 8


def evidence_key(item):
    return '|'.join(str(part) for part in identity_of(item))


def verified_entries(task_id, evidence):
    return {evidence_key(item): dict(item, task_id=task_id, verification='reviewer_passed')
            for item in evidence}


def raw_entries(task_id, observed):
    return {ref: dict(ref=ref, task_id=task_id, verification='unreviewed',
                      relative_path=item.get('relative_path'), content_sha256=item.get('content_sha256'),
                      quote_sha256=item.get('quote_sha256'))
            for ref, item in observed.items()}


def inference_entry(task_id, row):
    conclusion = row['conclusion']
    return dict(task_id=task_id, kind='agent_inference', decision=conclusion['decision'],
                unresolved_items=list(conclusion['unresolved_items']),
                uncertainty=dict(conclusion['uncertainty']))


def _quote(item):
    source = item.get('source')
    quote = source.get('quote') if isinstance(source, dict) else item.get('quote')
    return quote[:_QUOTE_LIMIT] if isinstance(quote, str) else None


def upstream_result(task_id, row):
    """What a dependent task may read of a verified upstream result: the conclusion and its cited evidence."""
    conclusion = row['conclusion']
    return dict(task_id=task_id, question=conclusion['question'], decision=conclusion['decision']['value'],
                evidence=[dict(source_id=item['source_id'], source_version=item['source_version'],
                               line_start=item['line_start'], line_end=item['line_end'], quote=_quote(item))
                          for item in row['evidence'][:_EVIDENCE_PER_TASK]])


def gaps_of(row):
    """Structured gaps a result reports. Read from fields, never parsed out of text."""
    conclusion = row['conclusion']
    gaps = list(conclusion['unresolved_items'])
    if conclusion['decision']['status'] == 'partial':
        gaps.append('PARTIAL_DECISION')
    uncertainty = conclusion['uncertainty']
    gaps.extend(uncertainty.get('unknown_fields', ()))
    if uncertainty['status'] in ('partial', 'insufficient_evidence', 'conflicting_evidence'):
        gaps.append(uncertainty['statement'] or 'UNCERTAINTY_' + uncertainty['status'].upper())
    return gaps
