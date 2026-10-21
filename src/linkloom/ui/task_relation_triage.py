"""Read-only pair navigation over validated task results, never relation authority.

Called only after the product's current-source, Ledger and Reviewer checks.
Discovery hints and semantic proposals cannot create a P4 result or receipt.
"""
from copy import deepcopy

from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash


def project_relation_triage(observations, discovery, proposals, relations, steps, source_mapping,
                            *, history_included=False, relation_results_truncated=False):
    refs = {}
    for observation in observations:
        ref = ObservationRef.from_observation(TaskObservation.from_dict(observation)).to_dict()
        refs[canonical_hash(ref)] = (ref, observation)
    checks = {}

    def bind(predicate, subject, target):
        if predicate not in {'SAME_TASK', 'RELATED_TO', 'REVISES_PLAN'}:
            return None
        keys = [canonical_hash(subject), canonical_hash(target)]
        if keys[0] == keys[1] or any(key not in refs for key in keys):
            return None
        # Exact immutable refs include workspace, version, evidence and payload.
        # Symmetry is inherited only from the existing SAME_TASK read contract.
        if predicate == 'SAME_TASK':
            keys.sort()
        key = (predicate, *keys)
        if key not in checks:
            endpoints = []
            for endpoint in keys:
                ref, observation = refs[endpoint]
                span = observation['provenance']
                endpoints.append(dict(ref=deepcopy(ref), raw_text=observation['raw_text'],
                    event_time=observation.get('event_time'), evidence_ref=span['evidence_ref'],
                    line_start=span['line_start'], line_end=span['line_end'],
                    **source_mapping[span['artifact_version_id']]))
            checks[key] = dict(check_id='task_check_' + canonical_hash(key), predicate=predicate,
                directed=predicate != 'SAME_TASK', endpoints=endpoints,
                candidates=[], proposals=[], relation_records=[], steps=[])
        return checks[key]

    for candidate in discovery.get('candidates', []):
        row = bind(candidate.get('predicate_hint'), candidate.get('subject'), candidate.get('object'))
        if row is not None and candidate.get('relation_status') == 'UNCONFIRMED':
            row['candidates'].append(deepcopy(candidate))
    for proposal in proposals:
        row = bind(proposal.get('predicate'), proposal.get('subject'), proposal.get('object'))
        if row is not None and proposal.get('evidence_status') == 'EVIDENCE_VALID':
            row['proposals'].append(deepcopy(proposal))
    for relation in relations:
        assessment = relation.get('assessment') or {}
        row = bind(assessment.get('predicate'), assessment.get('subject'), assessment.get('object'))
        if row is not None:
            row['relation_records'].append(deepcopy(relation))
    for step in steps:
        endpoints = step.get('endpoints', [])
        if step.get('kind') != 'relation' or len(endpoints) != 2:
            continue
        row = bind(step.get('predicate'), *endpoints)
        if row is not None:
            row['steps'].append(deepcopy(step))

    for row in checks.values():
        bound_steps, records = row['steps'], row['relation_records']
        # Preserve exact P4 dependency/scope decisions, including a blocked step
        # whose standalone relation record happens to be current_supported.
        reasons = {reason for step in bound_steps for reason in step.get('blocking_reasons', [])}
        reasons.update(reason for record in records for reason in record.get('blocking_reasons', []))
        invalid_record = any(record.get('persistent_lifecycle') != 'ACTIVE'
            or record.get('evidence_state') != 'VERIFIED' for record in records)
        p4_negative = any((record.get('assessment') or {}).get('verdict') in {'REFUTED', 'CONFLICT'}
            for record in records) or any(step.get('status') in {'REFUTED', 'CONFLICT'} for step in bound_steps)
        structural_block = any(reason not in {'NO_MATCHING_P4_RELATION', 'NO_RELATION_RECORDS',
            'P4_VERDICT_UNKNOWN'} for reason in reasons)
        established = bool(bound_steps) and all(step.get('status') == 'SUPPORTED'
            and step.get('current_supported') is True for step in bound_steps)
        model_positive = any(p.get('proposed_verdict') == 'SUPPORTED' and p.get('supporting_evidence')
            for p in row['proposals'])
        model_disagreement = any(p.get('proposed_verdict') in {'REFUTED', 'CONFLICT'} for p in row['proposals'])
        if established and not invalid_record and not p4_negative:
            row['state'] = 'ESTABLISHED'
            reasons.add('EXACT_CURRENT_P4_STEP_SUPPORTED')
        elif invalid_record or p4_negative or structural_block or model_disagreement:
            row['state'] = 'NOT_ESTABLISHED'
            if model_disagreement:
                reasons.add('UNCONFIRMED_MODEL_COUNTER_EVIDENCE')
        elif row['candidates'] or model_positive:
            row['state'] = 'POSSIBLE'
            reasons.add('UNCONFIRMED_HINT_ONLY')
        else:
            row['state'] = 'NOT_ESTABLISHED'
            reasons.add('NO_POSITIVE_RELATION_EVIDENCE')
        row['reason_codes'] = sorted(reasons)
        row['authority'] = 'READ_ONLY_PRESENTATION; NO_NEW_RELATION_OR_AUTHORIZATION'
        row['evidence'] = []

        def evidence(items, origin, role):
            for item in items:
                span = item.get('span') or {}
                mapping = source_mapping.get(span.get('artifact_version_id'))
                if mapping is None or span.get('content_hash') != mapping['content_sha256']:
                    continue
                row['evidence'].append(dict(origin=origin, role=role, quote=item['quote'],
                    evidence_ref=span['evidence_ref'], line_start=span['line_start'], line_end=span['line_end'],
                    **mapping))

        for candidate in row['candidates']:
            evidence(candidate.get('evidence', []), 'DISCOVERY_HINT', 'HINT')
        for proposal in row['proposals']:
            evidence(proposal.get('supporting_evidence', []), 'MODEL_PROPOSAL', 'SUPPORT')
            evidence(proposal.get('counter_evidence', []), 'MODEL_PROPOSAL', 'COUNTER')
        for record in records:
            assessment = record.get('assessment') or {}
            evidence(assessment.get('supporting_evidence', []), 'P4_RECORD', 'SUPPORT')
            evidence(assessment.get('counter_evidence', []), 'P4_RECORD', 'COUNTER')
        # Bound the display to evidence already returned by the existing tools.
        unique = {canonical_hash(item): item for item in row['evidence']}
        row['evidence'] = list(unique.values())
        # The complete audit dictionaries already live in task_review. Avoid
        # copying embedded observation snapshots/event histories into every card.
        row['candidates'] = [{key: c[key] for key in ('candidate_id', 'reason_codes', 'uncertainty')}
            for c in row['candidates']]
        row['proposals'] = [{key: p.get(key) for key in ('proposal_id', 'proposed_verdict',
            'evidence_status', 'reason_codes', 'missing_evidence')} for p in row['proposals']]
        row['relation_records'] = [dict(assessment_id=r['assessment']['assessment_id'],
            verdict=r['assessment']['verdict'], persistent_lifecycle=r['persistent_lifecycle'],
            evidence_state=r['evidence_state'], current_supported=r['current_supported'],
            blocking_reasons=r['blocking_reasons']) for r in records]
        row['steps'] = [{key: s.get(key) for key in ('step_id', 'question', 'status',
            'depends_on', 'blocking_reasons')} for s in bound_steps]
    return dict(checks=list(checks.values()),
        p4_history_included=history_included,
        p4_results_truncated=relation_results_truncated,
        candidate_discovery_truncated=bool(discovery.get('truncated')),
        candidate_discovery_gaps=list(discovery.get('truncation_reasons', [])),
        scope='RETURNED_PAIRS_ONLY; NOT_EXHAUSTIVE; NO_RELATION_ABSENCE_CLAIM')
