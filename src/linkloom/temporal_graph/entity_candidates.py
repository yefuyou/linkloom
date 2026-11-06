"""Candidate-only recovery from the Host's confirmed, time-scoped directory."""

from datetime import date, datetime
import re
import unicodedata

from linkloom.temporal_graph.dates import date_matches, date_tokens
from linkloom.temporal_graph.grounding import folded
from linkloom.temporal_graph.models import parse_time


# These floors only control which suggestions can be shown. They are never
# sufficient to bind a root. Reranker scores are logits, not probabilities.
CANDIDATE_SCORE_FLOOR = -2.7
CANDIDATE_SIMILARITY_FLOOR = 0.32


def literal_in(label, text):
    if not label:
        return False
    label = unicodedata.normalize('NFKC', label).casefold().strip()
    text = unicodedata.normalize('NFKC', text).casefold()
    if not label:
        return False
    prefix = r'(?<![a-z0-9_])' if label[0].isascii() and label[0].isalnum() else ''
    suffix = r'(?![a-z0-9_])' if label[-1].isascii() and label[-1].isalnum() else ''
    return re.search(prefix + re.escape(label) + suffix, text) is not None


def _witness(snapshot, selected, node_id, definition, when):
    """Only a distinct, literal qualifier can corroborate the weak match."""
    source = definition['source']
    before, after = source['question'][:source['start']], source['question'][source['end']:]
    records = {record['node_id']: record for record in snapshot.records}
    state = selected.get('current', {})
    labels = [(selected.get('subject'), ()), (state.get('fields', {}).get('owner'), ())]
    dates = [(state.get('valid_from') or selected.get('valid_from'), ())]
    for edge in snapshot.edges:
        if edge.blocker or not edge.evidence or edge.time_match(when) != 'VALID':
            continue
        if edge.target.node_id == node_id and edge.edge_type == 'alias_of':
            labels.append((edge.source.label, edge.evidence))
        if edge.source.node_id != node_id:
            continue
        related = records.get(edge.target.node_id, {})
        if edge.edge_type == 'derived_from':
            labels.append((related.get('subject'), edge.evidence))
        elif edge.edge_type in {'assigned_in', 'decided_in'}:
            dates.append((related.get('meeting_date'), edge.evidence))
    for label, proofs in labels:
        if label and len(folded(label)) >= 2 and not literal_in(label, definition['text']):
            if literal_in(label, before) or literal_in(label, after):
                return proofs
    # A matching date must be a full question token outside the entity span.
    # A bare lookup as_of or a date inside the mention is not a second signal.
    literals = {literal for literal, start, end in date_tokens(source['question'])
                if end <= source['start'] or start >= source['end']}
    for value, proofs in dates:
        if not value:
            continue
        parsed = parse_time(value)
        day = parsed.date() if isinstance(parsed, datetime) else parsed
        if isinstance(day, date) and any(date_matches(day.isoformat(), literal) for literal in literals):
            return proofs
    return None


def recover_candidates(snapshot, result, eligible, definition, when, limits):
    from linkloom.temporal_graph.meeting_query import _proof, _selected

    if (definition['kind'] not in {'task', 'decision_record'} or definition['reference'] != 'direct'
            or result['status'] != 'NOT_FOUND' or result.get('coverage') != 'complete'):
        return result
    ranked = {row['node_id']: row for row in result.get('candidates', [])}
    options = {}
    for node_id, entry in eligible.items():
        match = ranked.get(node_id)
        text = definition['text']
        significant = sum(character.isalnum() for character in text)
        minimum = 2 if any('\u4e00' <= character <= '\u9fff' for character in text) else 3
        partial_title = significant >= minimum and literal_in(text, entry['identity'])
        near = match and match['score'] >= CANDIDATE_SCORE_FLOOR and match['identity_similarity'] >= CANDIDATE_SIMILARITY_FLOOR
        if not (partial_title or near) or not entry['record']:
            continue
        selected = _selected(entry['record'], when)
        if _proof(selected if selected is not None else entry['record']):
            options[node_id] = selected
    if not options:
        return result
    if len(options) > min(4, limits.max_fanout):
        return dict(result, status='LIMIT_EXCEEDED', reason='ROOT_CANDIDATE_FANOUT_EXCEEDED')
    keys = sorted(options)
    # Do not discard competing candidates by applying the qualifier as a filter.
    # The normal resolver still checks blockers, time conflicts and evidence.
    if len(keys) == 1 and options[keys[0]] is not None:
        witness = _witness(snapshot, options[keys[0]], keys[0], definition, when)
        if witness is not None:
            return dict(result, status='FOUND', node_ids=keys, reason='CORROBORATED_ENTITY_CANDIDATE',
                        witness_evidence=[proof.to_dict() for proof in witness])
    return dict(result, status='AMBIGUOUS', node_ids=keys, reason='CONFIRMED_CANDIDATES_NEED_CLARIFICATION')
