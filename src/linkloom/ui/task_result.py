"""Present existing task Runtime artifacts without assigning semantic authority."""
from copy import deepcopy

from linkloom.ui.backend import project_reviewer


def _expand_references(value, *views):
    """Expand the existing lossless tool dictionaries for display, not new facts."""
    tables = {name: {} for name in ('evidence_spans', 'observation_refs', 'observation_snapshots')}
    for view in views:
        for name in tables:
            tables[name].update(view.get(name, {}))
    keys = {'span_ref': 'evidence_spans', 'observation_ref': 'observation_refs',
            'observation_snapshot_ref': 'observation_snapshots'}

    def visit(item):
        if isinstance(item, dict):
            for key, table in keys.items():
                if set(item) == {key}:
                    return visit(tables[table][item[key]])
            return {k: visit(v) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(v) for v in item]
        return item
    return visit(value)


def project_task_review(snapshot, preparation):
    """A presentation over existing observation/proposal/P4 contracts only.

    Called after current product sources and the Runtime result were validated.
    Original per-message fields remain separate even if their descriptions match.
    """
    payload = snapshot.get('task_investigation')
    if not payload or snapshot.get('reviewer', {}).get('decision') != 'evidence_sufficient':
        return None
    discovery = preparation['discovery']
    observations = [deepcopy(o) for o in
        (*discovery['valid_observations'], *discovery['partial_observations'])]
    evidence_refs = {e['evidence_ref'] for e in snapshot['evidence']}
    for observation in observations:
        span = observation['provenance']
        if span['evidence_ref'] not in evidence_refs:
            snapshot['evidence'].append(dict(evidence_ref=span['evidence_ref'],
                artifact_version_id=span['artifact_version_id'], artifact_id=span['artifact_id'],
                line_start=span['line_start'], line_end=span['line_end'], quote=span['quote'],
                quote_sha256=span['quote_sha256'], verified=True,
                authority='ORIGINAL_SOURCE_ONLY', **preparation['source_mapping'][span['artifact_version_id']]))
            evidence_refs.add(span['evidence_ref'])
    query = payload['relation_query']
    analysis = payload.get('semantic_analysis', {})
    proposals = []
    for proposal in analysis.get('proposals', []):
        if proposal.get('evidence_status') == 'EVIDENCE_VALID':
            proposals.append(_expand_references(proposal, analysis))
        else:
            # Show the failed candidate and its gap without presenting unchecked
            # quotes or reference bindings as source evidence.
            candidate = {key: deepcopy(proposal.get(key)) for key in
                ('predicate', 'proposed_verdict', 'evidence_status', 'semantic_status',
                 'missing_evidence', 'reason_codes')}
            candidate.update(subject={}, object={}, supporting_evidence=[],
                             counter_evidence=[], publishable=False)
            proposals.append(candidate)
    relations = [_expand_references(r, query) for r in query.get('relations', [])]
    plan = (preparation.get('planning') or [{}])[-1].get('plan') or {}
    p4_steps = {s['step_id']: deepcopy(s) for s in query.get('investigation_steps', [])}
    completed = {r['step_id']: r for r in plan.get('verified_results', [])}
    for row in snapshot.get('trace', []):
        if row['tool'] == 'inspect_task_context' and row['status'] == 'completed':
            for receipt in (row.get('result') or {}).get('value', {}).get('investigation_receipts', []):
                completed[receipt['step_id']] = receipt
    steps = []
    for step in plan.get('steps', []):
        receipt = completed.get(step['id'])
        row = p4_steps.get(step['id']) or dict(step_id=step['id'], kind=step['kind'],
            predicate=step['predicate'], depends_on=step['depends_on'],
            status=receipt['outcome'] if receipt else 'BLOCKED',
            blocking_reasons=[] if receipt else ['TRUSTED_RELATION_REQUIRED' if step['kind'] in
                {'relation', 'compare_dates'} else 'NO_EXECUTABLE_OPERATION'])
        row.update(question=step['question'], endpoints=deepcopy(step['endpoints']))
        steps.append(row)
    unresolved = [dict(step_id=s['step_id'], question=s['question'],
        reasons=s['blocking_reasons'], depends_on=s['depends_on'])
        for s in steps if s['status'] not in {'SUPPORTED', 'COMPLETED'}]
    next_actions = [dict(kind='REVIEW_ORIGINALS', step_id=s['step_id'], predicate=s['predicate'],
        observation_ids=[e['observation_id'] for e in s['endpoints'] if 'observation_id' in e])
        for s in steps if s['status'] not in {'SUPPORTED', 'COMPLETED'} and s['kind'] == 'relation']
    if not observations:
        next_actions.append(dict(kind='PROVIDE_TASK_SOURCE'))
    if any(s['kind'] == 'compare_dates' and s['status'] != 'COMPLETED' for s in steps):
        next_actions.append(dict(kind='IDENTITY_AND_REVISION_REQUIRED'))
    return dict(observations=observations, proposals=proposals, relations=relations,
        steps=steps, unresolved=unresolved, next_actions=next_actions,
        executed_tools=[dict(tool=r['tool'], call_id=r['call_id'], status=r['status'])
                        for r in snapshot.get('trace', [])],
        human_review=dict(available=False, reason='NO_AUTHENTICATED_RELATION_REVIEW_INTERFACE'),
        authority='SOURCE_OBSERVATIONS_AND_SEPARATE_P4_READS',
        scope='BOUNDED_SNAPSHOT; NO_GLOBAL_CURRENT_OWNER_OR_DEADLINE_CLAIM')


def project_task_snapshot(query, workspace, status, state, artifact):
    review = project_reviewer(artifact)
    accepted = review.get('decision') == 'evidence_sufficient'
    payload = (artifact or {}).get('result', {}).get('task_relations') if accepted else None
    running = status.status in {'accepted', 'running', 'paused'}
    error = deepcopy((artifact or {}).get('error') or status.error)
    snapshot = dict(query=query, run=dict(id=status.run_id, thread_id=status.thread_id),
        kind='running' if running else 'insufficient' if payload else 'error',
        reviewer=review, task_investigation=deepcopy(payload), evidence=[],
        uncertainty=dict(unknown_fields=['semantic_acceptance'],
            statement='Task observations and model analyses are unconfirmed; semantic and business acceptance are NOT_EVALUATED.'),
        error=error, progress=None, read_only=True)
    ledger = state.tool_ledger if state else ()
    snapshot['trace'] = [dict(sequence=r.sequence, call_id=r.call_id, tool=r.tool_id,
        status=r.status, arguments=r.arguments, result=r.result, error=r.error) for r in ledger]
    snapshot['metrics'] = dict(model_turns=len(state.model_executions) if state else 0,
        tool_calls=len(ledger), tool_errors=sum(r.status == 'failed' for r in ledger))
    if artifact and artifact.get('working_context_audit'):
        snapshot['working_context_audit'] = artifact['working_context_audit']
    if payload:
        # These are original source quotes, not confirmation of task semantics.
        observations = payload['context'].get('observations', [])
        spans = {o['observation']['provenance']['evidence_ref']: o['observation']['provenance']
                 for o in observations}
        analysis = payload.get('semantic_analysis', {})
        snapshot['uncertainty']['model_reported_gaps'] = [gap for proposal in analysis.get('proposals', [])
            for gap in proposal.get('missing_evidence', [])]
        snapshot['uncertainty']['stage_errors'] = list(analysis.get('errors', []))
        snapshot['uncertainty']['stage_errors'].extend(
            error for proposal in analysis.get('proposals', [])
            if proposal.get('evidence_status') != 'EVIDENCE_VALID'
            for error in proposal.get('source_validation', {}).get('errors', ['SEMANTIC_EVIDENCE_INVALID']))
        for proposal in analysis.get('proposals', []):
            if proposal.get('evidence_status') != 'EVIDENCE_VALID':
                continue
            for citation in (*proposal.get('supporting_evidence', []), *proposal.get('counter_evidence', [])):
                span = citation.get('span')
                if span and 'span_ref' in span:
                    span = analysis.get('evidence_spans', {}).get(span['span_ref'])
                if span:
                    spans[span['evidence_ref']] = span
        snapshot['evidence'] = [dict(evidence_ref=s['evidence_ref'],
            artifact_version_id=s['artifact_version_id'], artifact_id=s['artifact_id'],
            line_start=s['line_start'], line_end=s['line_end'], quote=s['quote'],
            content_sha256=s['content_hash'], quote_sha256=s['quote_sha256'],
            verified=True, authority='ORIGINAL_SOURCE_ONLY') for s in spans.values()]
    return snapshot
