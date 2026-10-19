"""Present existing task Runtime artifacts without assigning semantic authority."""
from copy import deepcopy

from linkloom.ui.backend import project_reviewer


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
