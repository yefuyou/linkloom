"""Read-only adapter for persisted legacy observations in the product SQLite file."""
from __future__ import annotations

import json

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.tasks.models import EvidenceReference, TaskCandidateInput, canonical_json, required_text
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, digest


class LegacyTaskCandidateSource:
    """No models, memory registry, or Product/decision workflow mutation.

    Only this adapter imports legacy serialization types. The complete original
    payload is retained, while ingestion timestamps are excluded from semantic
    identity. Every call uses the caller's active SQLite write transaction.
    """

    def list_candidates(self, connection, workspace_id):
        workspace = required_text(workspace_id, 'workspace_id')
        if not connection.in_transaction:
            raise ValueError('candidate verification requires an active transaction')
        if connection.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?', (workspace,)).fetchone() is None:
            raise CandidateUnavailableError('workspace is unavailable')
        rows = connection.execute('''SELECT candidate.* FROM semantic_candidate AS candidate
            JOIN ll_source AS source ON source.workspace_id=candidate.workspace_id
                AND source.source_id=candidate.artifact_id
                AND source.current_version_id=candidate.artifact_version_id
            WHERE candidate.workspace_id=? AND candidate.candidate_source_kind='SOURCE_INGESTION'
                AND source.active=1 ORDER BY candidate.candidate_id''', (workspace,)).fetchall()
        groups = {}
        contexts = {}
        for row in rows:
            try:
                parsed = deserialize_candidate(row['payload_json'])
                encoded, fingerprint = serialize_candidate(parsed)
                payload = json.loads(encoded)
                if (encoded != row['payload_json'] or fingerprint != row['candidate_fingerprint']
                    or payload['record_version'] != row['payload_version']
                    or any(getattr(parsed, name) != row[column] for name, column in (
                        ('candidate_id', 'candidate_id'), ('workspace_id', 'workspace_id'),
                        ('artifact_id', 'artifact_id'), ('artifact_version_id', 'artifact_version_id'),
                        ('content_sha256', 'content_sha256'), ('extraction_fingerprint', 'extraction_fingerprint')))):
                    raise ValueError('stored candidate integrity mismatch')
                observation = parsed.task_observation
                if observation is None:
                    continue
                if observation.provenance.workspace_id != workspace or parsed.workspace_id != workspace:
                    raise ValueError('observation belongs to another workspace')
                version = connection.execute('''SELECT * FROM ll_source_version
                    WHERE workspace_id=? AND source_id=? AND artifact_version_id=?''',
                    (workspace, parsed.artifact_id, parsed.artifact_version_id)).fetchone()
                if version is None:
                    raise ValueError('source version is unavailable')
                artifact = RawArtifact(workspace_id=workspace, artifact_id=parsed.artifact_id,
                    source_type='timestamped_text', content=version['content'], source_uri=version['source_uri'])
                if (artifact.artifact_version_id != version['artifact_version_id']
                    or artifact.content_hash != version['content_sha256']
                    or artifact.content_hash != parsed.content_sha256 or not observation.verify(artifact)):
                    raise ValueError('observation does not verify against the complete original source')
                # Keep ALL semantic fields, including validation and field evidence.
                semantic = json.loads(encoded)
                semantic['candidate'].pop('ingestion_time')
                semantic['candidate']['task_observation'].pop('recorded_at')
                source_fingerprint = digest(semantic)
                span = observation.provenance
                saved_evidence = connection.execute('''SELECT * FROM ll_source_evidence
                    WHERE workspace_id=? AND source_id=? AND artifact_version_id=? AND evidence_ref=?''',
                    (workspace, span.artifact_id, span.artifact_version_id, span.evidence_ref)).fetchone()
                if saved_evidence is None or any(saved_evidence[column] != value for column, value in (
                    ('content_sha256', span.content_hash), ('quote_sha256', span.quote_sha256),
                    ('quote', span.quote), ('line_start', span.line_start), ('line_end', span.line_end))):
                    raise ValueError('source evidence catalogue is missing or no longer matches')
                evidence = EvidenceReference(workspace, span.artifact_id, span.artifact_version_id,
                    span.quote, span.quote_sha256, message_id=span.message_id,
                    line_start=span.line_start, line_end=span.line_end, char_start=span.char_start,
                    char_end=span.char_end, source_sha256=span.content_hash,
                    meeting_time=observation.event_time)
                candidate = TaskCandidateInput(observation.observation_id, workspace, observation.description,
                    (evidence,), owner=observation.owner, deadline=observation.deadline,
                    deadline_text=observation.deadline_text, meeting_time=observation.event_time,
                    candidate_version='legacy-task-observation/v1', source_fingerprint=source_fingerprint)
                groups.setdefault(candidate.candidate_id, []).append((candidate, encoded, row['workflow_state']))
                if span.artifact_id not in contexts:
                    versions = [tuple(item) for item in connection.execute('''SELECT rowid,artifact_version_id,content_sha256
                        FROM ll_source_version WHERE workspace_id=? AND source_id=? ORDER BY rowid''',
                        (workspace, span.artifact_id))]
                    epoch = connection.execute('''SELECT COALESCE(MAX(sequence),0) FROM task_review_source_change
                        WHERE workspace_id=? AND source_id=?''', (workspace, span.artifact_id)).fetchone()[0]
                    contexts[span.artifact_id] = dict(source_id=span.artifact_id,
                        current_version=span.artifact_version_id, versions=versions, epoch=epoch)
            except (TypeError, KeyError, ValueError) as error:
                raise CandidateUnavailableError('legacy candidate or original source failed verification') from error
        results = []
        for variants in groups.values():
            signatures = sorted({candidate.source_fingerprint for candidate, _, _ in variants})
            for candidate, encoded, workflow_state in variants:
                if workflow_state != 'SUPPORTING_ONLY':
                    continue
                context = dict(source=contexts[candidate.evidence[0].source_id], variants=signatures)
                results.append(CandidateSnapshot(candidate, digest(context), encoded, len(signatures) > 1))
        return tuple(results)