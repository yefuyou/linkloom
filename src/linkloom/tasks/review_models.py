"""Extractor-neutral review boundaries; source adapters verify on the supplied transaction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Protocol

from linkloom.tasks.models import TaskCandidateInput, canonical_json, required_text, _hash


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


class CandidateUnavailableError(ValueError):
    """Current source data cannot authorize the requested candidate."""


class ReviewConflictError(ValueError):
    """A fresh preview or explicit re-review is required."""


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    candidate: TaskCandidateInput
    context_token: str
    original_payload_json: str
    extraction_conflict: bool = False

    def __post_init__(self):
        if not isinstance(self.candidate, TaskCandidateInput):
            raise ValueError('candidate must use the unified input contract')
        _hash(self.context_token, 'context_token')
        if not isinstance(json.loads(self.original_payload_json), dict):
            raise ValueError('original candidate payload must be an object')
        if type(self.extraction_conflict) is not bool:
            raise ValueError('extraction_conflict must be boolean')

    @property
    def fingerprint(self) -> str:
        return digest([self.candidate.fingerprint, self.context_token])

    def to_dict(self):
        return dict(candidate=self.candidate.to_dict(), context_token=self.context_token,
                    original_payload=json.loads(self.original_payload_json),
                    extraction_conflict=self.extraction_conflict, fingerprint=self.fingerprint)

    @classmethod
    def from_dict(cls, value):
        snapshot = cls(TaskCandidateInput.from_dict(value['candidate']), value['context_token'],
                       canonical_json(value['original_payload']), value['extraction_conflict'])
        if snapshot.fingerprint != value['fingerprint']:
            raise ValueError('stored review candidate fingerprint mismatch')
        return snapshot


class CandidateSource(Protocol):
    def list_candidates(self, connection: sqlite3.Connection, workspace_id: str) -> tuple[CandidateSnapshot, ...]:
        """Read and verify current candidates using this exact active transaction.

        Fingerprints must bind the complete semantic payload and all conflict
        variants. Context tokens must bind each evidence source's durable change
        sequence and version catalogue. No in-memory registry is an authority.
        """
        ...


def identity(workspace_id, reviewer, reason):
    return (required_text(workspace_id, 'workspace_id'), required_text(reviewer, 'reviewer'),
            required_text(reason, 'reason'))


def task_fields(task):
    return dict(title=task.title, owner=task.owner,
                deadline=dict(deadline=task.deadline, deadline_text=task.deadline_text))

@dataclass(frozen=True, slots=True)
class SourceVersionProof:
    """Verified immutable source version and durable catalogue order.

    version_sequence is the source catalogue's actual SQLite insertion rowid,
    never a timestamp or a lexical version-ID comparison. evidence_fingerprint
    binds all exact references verified by the adapter in the active transaction.
    """

    workspace_id: str
    source_id: str
    source_version_id: str
    version_sequence: int
    content_sha256: str
    meeting_times: tuple
    context_token: str
    evidence_fingerprint: str
    is_current: bool

    def __post_init__(self):
        from linkloom.tasks.models import utc_time
        for field in ('workspace_id', 'source_id', 'source_version_id'):
            object.__setattr__(self, field, required_text(getattr(self, field), field))
        if type(self.version_sequence) is not int or self.version_sequence < 1:
            raise ValueError('source version sequence must be a positive catalogue rowid')
        for field in ('content_sha256', 'context_token', 'evidence_fingerprint'):
            _hash(getattr(self, field), field)
        if not isinstance(self.meeting_times, tuple) or type(self.is_current) is not bool:
            raise ValueError('invalid source proof')
        object.__setattr__(self, 'meeting_times', tuple(utc_time(value, 'meeting_time') for value in self.meeting_times))

    def to_dict(self):
        return dict(workspace_id=self.workspace_id, source_id=self.source_id,
            source_version_id=self.source_version_id, version_sequence=self.version_sequence,
            content_sha256=self.content_sha256, meeting_times=[value.isoformat() for value in self.meeting_times],
            context_token=self.context_token, evidence_fingerprint=self.evidence_fingerprint,
            is_current=self.is_current)


class SourceVersionReader(Protocol):
    def version_proof(self, connection: sqlite3.Connection, workspace_id: str, source_id: str,
                      source_version_id: str, *, evidence=(), require_current=False) -> SourceVersionProof:
        """Verify complete original content and every reference on this connection."""
        ...