"""Bind Supervisor review judgments to actual, reviewed Worker citations."""
from __future__ import annotations

import hashlib

from linkloom.orchestration.state import ACCEPTED
from linkloom.ui.branch_synthesis import identity_of


VERDICTS = frozenset({'SUPPORTS', 'CONTRADICTS', 'IRRELEVANT', 'INSUFFICIENT', 'AMBIGUOUS'})
JUDGMENT_FIELDS = frozenset({'item_id', 'claim_id', 'citation_id', 'verdict', 'supporting_quote', 'explanation'})


def required_items(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        return {}
    items = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != {'id', 'question'}:
            return {}
        key, question = row['id'], row['question']
        if (not isinstance(key, str) or not 1 <= len(key) <= 40 or not key.isascii()
                or not key.replace('_', '').replace('-', '').isalnum()
                or key in items or not isinstance(question, str) or not question.strip() or len(question) > 500):
            return {}
        items[key] = question.strip()
    return items


def _verified_span(deps, item):
    source_id, version, start, end, quote_hash = identity_of(item)
    path = item.get('relative_path')
    known = deps.source_versions.get(path)
    if not known or (source_id, version) != (known['source_id'], known['source_version']):
        return None
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        return None
    try:
        data = (deps.vault / path).read_bytes()
        lines = data.decode('utf-8').splitlines()
    except (OSError, UnicodeError):
        return None
    quote = item.get('source', {}).get('quote')
    if (not isinstance(quote, str) or end > len(lines) or '\n'.join(lines[start-1:end]) != quote
            or hashlib.sha256(data).hexdigest() != item.get('content_sha256')
            or hashlib.sha256(quote.encode('utf-8')).hexdigest() != quote_hash):
        return None
    return quote


def review_operands(deps, state, evidence_index):
    operands = {}
    items = state.get('required_items') or {}
    for branch, task in state.get('tasks', {}).items():
        row = (state.get('rows') or {}).get(branch)
        if task['status'] != ACCEPTED or not row or not row['run'].get('review_id'):
            continue
        evidence = {identity_of(item): item for item in row['evidence']}
        for claim in row['conclusion'].get('item_claims', []):
            item_id = claim['item_id']
            if item_id not in items or item_id not in task.get('item_ids', []):
                continue
            for ref in claim['evidence']:
                key = identity_of(ref)
                if key not in evidence or key not in evidence_index:
                    continue
                item, merged = evidence[key], evidence_index[key]
                if item.get('content_sha256') != merged.get('content_sha256'):
                    continue
                quote = _verified_span(deps, item)
                if quote is None:
                    continue
                cid = merged['id']
                claim_id = claim['claim_id']
                operands[item_id, claim_id, cid] = dict(
                    item_id=item_id, claim_id=claim_id, citation_id=cid, branch=branch,
                    fact=claim['fact'], quote=quote, source_id=key[0], source_version=key[1],
                    line_start=key[2], line_end=key[3], quote_sha256=key[4],
                    content_sha256=item['content_sha256'])
    return operands


def validated_judgments(operands, proposed):
    if not isinstance(proposed, list) or len(proposed) > 64:
        return []
    accepted, seen, duplicates = [], set(), set()
    for row in proposed:
        if (not isinstance(row, dict) or set(row) != JUDGMENT_FIELDS
                or any(not isinstance(v, str) or not v.strip() for v in row.values())):
            continue
        key = (row['item_id'], row['claim_id'], row['citation_id'])
        if key in seen:
            duplicates.add(key)
        seen.add(key)
        operand = operands.get(key)
        if (operand and row['verdict'] in VERDICTS and row['supporting_quote'] in operand['quote']):
            accepted.append(dict(operand, verdict=row['verdict'], supporting_quote=row['supporting_quote'],
                                 explanation=row['explanation'], support='CITATION_VERIFIED'))
    return [row for row in accepted if (row['item_id'], row['claim_id'], row['citation_id']) not in duplicates]
