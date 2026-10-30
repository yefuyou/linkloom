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