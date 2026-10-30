"""V2-only decoding boundary for already generated meeting-task batches."""
from __future__ import annotations

from dataclasses import asdict
import json
import re

from linkloom.semantic_ingestion.artifacts import EvidenceSpan, RawArtifact
from linkloom.semantic_ingestion.meeting_tasks_v2 import Deadline, MeetingTaskBatch, SUPPORTS, VERSION
from linkloom.semantic_ingestion.timestamped_text import ParseStatus, parse_timestamped_text
from linkloom.tasks.candidate_store import PreparedBatch, PreparedCandidate
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import EvidenceReference, TaskCandidateInput, canonical_json, required_text
from linkloom.tasks.review_models import CandidateUnavailableError, digest


_CANDIDATE_FIELDS = {'candidate_id', 'task', 'owner_ids', 'deadline', 'evidence', 'adopted', 'scope',
    'end_state', 'deliverable_key', 'reason', 'member_ids', 'deadline_variants', 'validation_issues'}
_BATCH_FIELDS = {'version', 'candidates', 'excluded', 'rejected', 'windows', 'coverage_complete', 'responses'}


def _shape(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'invalid {name} structure')
    return value


def _text(value, name, maximum):
    required_text(value, name)
    if len(value) > maximum:
        raise ValueError(f'{name} exceeds its contract limit')
    return value


def _texts(value, name, *, required=False, maximum=32):
    if not isinstance(value, list) or len(value) > maximum or (required and not value):
        raise ValueError(f'invalid {name} list')
    for item in value:
        _text(item, name, 512)
    if len(set(value)) != len(value):
        raise ValueError(f'{name} contains duplicates')
    return value


def _deadline(value):
    _shape(value, {'raw_text', 'kind', 'resolved_at'}, 'deadline')
    return Deadline(**value)


class MeetingV2BatchAdapter:
    """No v3 assumptions or model calls. Confidence is external neutral metadata."""
    format_version = VERSION

    def encode(self, batch):
        if not isinstance(batch, MeetingTaskBatch):
            raise ValueError('only an already generated MeetingTaskBatch v2 is accepted')
        return batch.to_dict()

    def prepare(self, connection, workspace_id, source_id, source_version_id, payload, confidence):
        if not connection.in_transaction:
            raise ValueError('batch source verification requires an active transaction')
        if connection.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?', (workspace_id,)).fetchone() is None:
            raise CandidateUnavailableError('workspace is unavailable')
        _shape(payload, _BATCH_FIELDS, 'v2 batch')
        if payload['version'] != VERSION or type(payload['coverage_complete']) is not bool:
            raise ValueError('unknown batch version or invalid coverage')
        for field in ('candidates', 'excluded', 'rejected', 'windows', 'responses'):
            if not isinstance(payload[field], list):
                raise ValueError(f'{field} must be a list')
        if len(payload['windows']) != len(payload['responses']):
            raise ValueError('windows and responses must have identical inventories')
        for item in payload['rejected']:
            _shape(item, {'window', 'candidate_index', 'reason'}, 'rejection diagnostic')
            if any(type(item[key]) is not int or item[key] < 0 for key in ('window', 'candidate_index')):
                raise ValueError('invalid rejection position')
            if item['window'] >= len(payload['windows']):
                raise ValueError('rejection references an absent window')
            _text(item['reason'], 'rejection reason', 2048)
        for item in payload['responses']:
            if (not isinstance(item, dict) or set(item) != {'coverage_complete', 'tasks'}
                or type(item['coverage_complete']) is not bool or not isinstance(item['tasks'], list) or len(item['tasks']) > 80):
                raise ValueError('invalid original response envelope')
        row = connection.execute("""SELECT * FROM ll_source_version WHERE workspace_id=? AND source_id=?
            AND artifact_version_id=?""", (workspace_id, source_id, source_version_id)).fetchone()
        if row is None:
            raise CandidateUnavailableError('source version is unavailable')
        artifact = RawArtifact(workspace_id, source_id, 'timestamped_text', row['content'], source_uri=row['source_uri'])
        parsed = parse_timestamped_text(artifact)
        if parsed.status != ParseStatus.COMPLETE:
            raise CandidateUnavailableError('v2 requires complete canonical original speaker parsing')
        source_reader = LegacyTaskCandidateSource()
        proof = source_reader.version_proof(connection, workspace_id, source_id, source_version_id, require_current=True)
        for window in payload['windows']:
            _shape(window, {'first_ordinal', 'last_ordinal', 'whole_meeting'}, 'window')
            if (type(window['first_ordinal']) is not int or type(window['last_ordinal']) is not int
                or not 0 <= window['first_ordinal'] <= window['last_ordinal'] < len(parsed.segments)
                or type(window['whole_meeting']) is not bool
                or (window['whole_meeting'] and (window['first_ordinal'] != 0 or window['last_ordinal'] != len(parsed.segments)-1))):
                raise ValueError('invalid canonical window bounds')
        covered = {ordinal for window in payload['windows']
                   for ordinal in range(window['first_ordinal'], window['last_ordinal']+1)}
        if payload['coverage_complete'] and (len(covered) != len(parsed.segments) or payload['rejected']
                or any(not item['coverage_complete'] or len(item['tasks']) >= 80 for item in payload['responses'])):
            raise ValueError('coverage claim contradicts its recorded diagnostics')
        items, identifiers = [], set()
        for reviewable, field in ((True, 'candidates'), (False, 'excluded')):
            for value in payload[field]:
                item = self._candidate(value, artifact, parsed.segments, payload['coverage_complete'], confidence, reviewable)
                if any(reference.message_id not in {segment.message_id for segment in parsed.segments if segment.ordinal in covered}
                       for reference in item.candidate.evidence):
                    raise ValueError('candidate cites outside the recorded windows')
                identifier = item.candidate.candidate_id
                if identifier in identifiers:
                    raise ValueError('duplicate candidate identity in batch')
                identifiers.add(identifier)
                source_reader.version_proof(connection, workspace_id, source_id, source_version_id,
                                            evidence=item.candidate.evidence, require_current=True)
                items.append(item)
        if set(confidence) - identifiers:
            raise ValueError('confidence supplied for a candidate outside the batch')
        return PreparedBatch(proof.context_token, tuple(items))

    @staticmethod
    def _candidate(value, artifact, segments, coverage, confidence, reviewable):
        _shape(value, _CANDIDATE_FIELDS, 'meeting candidate')
        identifier = _text(value['candidate_id'], 'candidate_id', 256)
        if re.fullmatch(r'meeting_(?:task|group)_[0-9a-f]{32}', identifier) is None:
            raise ValueError('invalid v2 candidate identity')
        _text(value['task'], 'task', 2048)
        _text(value['deliverable_key'], 'deliverable_key', 256)
        _text(value['reason'], 'reason', 2048)
        owners = _texts(value['owner_ids'], 'owner_ids')
        _texts(value['member_ids'], 'member_ids', required=True, maximum=4800)
        _texts(value['validation_issues'], 'validation_issues', maximum=80)
        if (type(value['adopted']) is not bool or value['scope'] not in ('AFTER_MEETING', 'IN_MEETING', 'UNCERTAIN')
            or value['end_state'] not in ('PENDING', 'COMPLETED', 'CANCELLED', 'UNCERTAIN')):
            raise ValueError('invalid candidate work state')
        pending = value['adopted'] and value['scope'] == 'AFTER_MEETING' and value['end_state'] == 'PENDING'
        if pending != reviewable:
            raise ValueError('candidate is in the wrong pending/excluded inventory')
        deadline = _deadline(value['deadline'])
        if not isinstance(value['deadline_variants'], list):
            raise ValueError('invalid deadline variant inventory')
        variants = tuple(_deadline(item) for item in value['deadline_variants'])
        if len(set(variants)) != len(variants):
            raise ValueError('duplicate deadline variants')
        if len(variants) > 1 and deadline.raw_text is not None:
            raise ValueError('conflicting raw deadlines must remain unresolved')
        references, supports = [], []
        if not isinstance(value['evidence'], list) or not value['evidence']:
            raise ValueError('candidate needs exact original evidence')
        for item in value['evidence']:
            _shape(item, {'span', 'supports'}, 'task evidence')
            _shape(item['span'], set(EvidenceSpan.__dataclass_fields__), 'evidence span')
            span = EvidenceSpan(**item['span'])
            roles = _texts(item['supports'], 'supports', required=True, maximum=5)
            if any(role not in SUPPORTS for role in roles):
                raise ValueError('unsupported evidence role')
            if not span.verify(artifact) or span.ordinal >= len(segments):
                raise ValueError('original v2 evidence failed verification')
            segment = segments[span.ordinal]
            if asdict(span) != asdict(segment.evidence_span):
                raise ValueError('evidence must be an entire canonical original message')
            references.append(EvidenceReference(artifact.workspace_id, artifact.artifact_id, artifact.artifact_version_id,
                span.quote, span.quote_sha256, message_id=span.message_id, line_start=span.line_start, line_end=span.line_end,
                char_start=span.char_start, char_end=span.char_end, source_sha256=span.content_hash,
                meeting_time=segment.event_time))
            supports.append(roles)
        if not any('work' in roles for roles in supports) or (owners and not any('owner' in roles for roles in supports)):
            raise ValueError('candidate lacks required field witnesses')
        for stated in (deadline, *variants):
            if stated.raw_text is not None and not any('deadline' in roles and stated.raw_text in reference.quote
                    for reference, roles in zip(references, supports)):
                raise ValueError('deadline wording lacks an exact deadline witness')
        # A spanning task has no single original recording timestamp. Preserve
        # each witness time, without guessing a meeting identity or import time.
        times = {reference.meeting_time for reference in references}
        meeting_time = next(iter(times)) if len(times) == 1 else None
        metadata = confidence.get(identifier)
        original = canonical_json(dict(format_version=VERSION, candidate=value, coverage_complete=coverage))
        native_fingerprint = digest([json.loads(original), metadata.to_dict() if metadata else None])
        candidate = TaskCandidateInput(identifier, artifact.workspace_id, value['task'], tuple(references),
            owner=owners[0] if len(owners) == 1 else None, deadline=None, deadline_text=deadline.raw_text,
            meeting_time=meeting_time, candidate_version=VERSION, source_fingerprint=native_fingerprint,
            **(metadata.to_dict() if metadata else {}))
        return PreparedCandidate(candidate, original, reviewable)
