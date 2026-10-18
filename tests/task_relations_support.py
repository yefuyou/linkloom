"""Allowlisted public input only. No Gold paths, labels or expected verdicts."""
from contextlib import ExitStack
from datetime import UTC, datetime
import json
from pathlib import Path

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote, ProjectRef
from linkloom.semantic_ingestion.task_relation_context import assess_relation, source_context_quote
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task

INPUT = Path(__file__).parent / 'fixtures/task_relations_p2/inputs/dev.json'


def public_cases():
    data = json.loads(INPUT.read_text(encoding='utf8'))
    if data['schema_version'] != 'task-relations-p2-input/v1' or data['split'] != 'dev':
        raise ValueError('unsupported public input')
    return data['cases']


def run_case(case, tmp_path):
    workspace = case['workspace_id']
    registry = SourceReferenceRegistry()
    artifacts = {s['artifact_id']: RawArtifact(workspace_id=workspace, **s,
        ingestion_time=datetime(2026,10,18,tzinfo=UTC)) for s in case['sources']}
    parsed = {name: parse_source_text(a) for name,a in artifacts.items()}
    for name, result in parsed.items():
        for segment in result.segments:
            registry.register_evidence(workspace, segment.evidence_span.evidence_ref,
                content_hash=artifacts[name].content_hash, source_version=artifacts[name].artifact_version_id)
    observations = {}
    for item in case['observations']:
        artifact = artifacts[item['source_id']]
        segment = parsed[item['source_id']].segments[item['segment_index']]
        obs = build_task_observation(TaskProposal.from_dict(item['task']), segment, artifact)
        if not obs.verify(artifact):
            raise ValueError('invalid public M1 observation')
        observations[item['label']] = obs
    before = {label: obs.to_dict() for label,obs in observations.items()}
    evidence_failures = []

    def quote_for(item):
        artifact = artifacts[item['source_id']]
        if item['segment_index'] is None:
            context = source_context_quote(artifact)
            span = context.span
            registry.register_evidence(workspace, span.evidence_ref,
                content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
        else:
            span = parsed[item['source_id']].segments[item['segment_index']].evidence_span
        if span.quote.count(item['quote']) != 1:
            evidence_failures.append({'source_id': item['source_id'], 'quote': item['quote'], 'readback_ok': False})
            return None
        start = span.char_start + span.quote.index(item['quote'])
        return EvidenceQuote(span, start, start + len(item['quote']), item['quote'], 'RELATION_PROPOSAL')

    predictions, excluded, audit = [], [], {}
    with ExitStack() as stack:
        store, reviewed = None, None
        if case.get('review_decision_setup'):
            from tests.task_change_support import create_reviewed_change
            q = next(q for q in case['questions'] if q.get('authority') == 'REVIEWED_DECISION')
            change = q['object']
            store, reviewed, audit = stack.enter_context(create_reviewed_change(tmp_path,
                artifacts[change['source_id']], registry, change['project'], change['before'], change['after']))

        def endpoint(data):
            if data['type'] == 'task':
                return observations[data['label']]
            if data['type'] == 'project':
                return ProjectRef(data['workspace_id'], data['name'])
            if data.get('decision_id'):
                if reviewed is None:
                    raise ValueError('no actual reviewed change')
                return reviewed
            span = parsed[data['source_id']].segments[data['segment_index']].evidence_span
            return ChangeRef(workspace, data['project'], data['before'], data['after'],
                EvidenceQuote(span, span.char_start, span.char_end, span.quote, 'CHANGE_CLAIM'))

        aliases = []
        changes_before = store._connection.total_changes if store else 0
        for question in case['questions']:
            if question['predicate'] == 'REVISES_PLAN':
                excluded.append({'question_id': question['question_id'], 'reason': 'OUTSIDE_P2_NOT_IMPLEMENTED'})
                continue
            subject, object = endpoint(question['subject']), endpoint(question['object'])
            proposals = [quote_for(p) for p in question['evidence']]
            if question['predicate'] == 'SAME_TASK':
                result = assess_same_task(subject, object, artifacts=list(artifacts.values()),
                    source_registry=registry, context_observations=tuple(observations.values()))
            else:
                result = assess_relation(question['predicate'], subject, object, artifacts=list(artifacts.values()),
                    source_registry=registry, evidence=proposals, context_observations=tuple(observations.values()),
                    alias_assessments=aliases, decision_store=store)
                if result.predicate == 'PROJECT_ALIAS':
                    aliases.append(result)
            predictions.append({'question_id': question['question_id'], 'assessment': result.to_dict()})
        if store is not None:
            audit['decisions_after_assessment'] = len(store.list_decisions(workspace))
            audit['database_writes_during_assessment'] = store._connection.total_changes - changes_before
            assert audit['database_writes_during_assessment'] == 0
    assert before == {label: obs.to_dict() for label,obs in observations.items()}
    return {'case_id': case['case_id'], 'predictions': predictions, 'excluded': excluded,
            'observations_unchanged': True, 'source_parse_status': {n:p.status.value for n,p in parsed.items()},
            'evidence_input_failures': evidence_failures, 'authority_audit': audit,
            'sources': [dict(workspace_id=a.workspace_id, artifact_id=a.artifact_id,
                artifact_version_id=a.artifact_version_id, content_hash=a.content_hash, content=a.content) for a in artifacts.values()]}
