"""Extractor-neutral contracts for human-confirmed tasks and their evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any


def required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{name} must be non-empty text')
    return value.strip()


def optional_text(value: object, name: str) -> str | None:
    return None if value is None else required_text(value, name)


def utc_time(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    return value.astimezone(UTC)


def deadline_value(value: object) -> str | None:
    if value is None:
        return None
    text = required_text(value, 'deadline')
    if date.fromisoformat(text).isoformat() != text:
        raise ValueError('deadline must be an explicit ISO calendar date')
    return text


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError(f'{name} must be a lowercase SHA-256 digest')
    return value


def _shape(data: object, names: set[str], name: str) -> dict:
    if not isinstance(data, dict) or set(data) != names:
        raise ValueError(f'{name} contains missing or unsupported fields')
    return data


def _time_from_text(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f'{name} must be an ISO timestamp')
    return utc_time(datetime.fromisoformat(value), name)


class TaskStatus(StrEnum):
    OPEN = 'open'
    IN_PROGRESS = 'in_progress'
    BLOCKED = 'blocked'
    DONE = 'done'
    CANCELLED = 'cancelled'


class TaskEventType(StrEnum):
    CREATED = 'created'
    OWNER_CHANGED = 'owner_changed'
    DEADLINE_CHANGED = 'deadline_changed'
    STATUS_CHANGED = 'status_changed'
    TITLE_CHANGED = 'title_changed'


class ReviewAction(StrEnum):
    CREATE = 'create'
    MERGE = 'merge'
    REJECT = 'reject'


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """One exact quotation. Hash checks do not replace source authorization."""

    workspace_id: str
    source_id: str
    source_version_id: str
    quote: str
    quote_sha256: str
    message_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    source_sha256: str | None = None
    meeting_time: datetime | None = None

    def __post_init__(self) -> None:
        for name in ('workspace_id', 'source_id', 'source_version_id'):
            object.__setattr__(self, name, required_text(getattr(self, name), name))
        if not isinstance(self.quote, str) or not self.quote.strip() or '\x00' in self.quote:
            raise ValueError('quote must preserve a non-empty source excerpt')
        _hash(self.quote_sha256, 'quote_sha256')
        if hashlib.sha256(self.quote.encode('utf-8')).hexdigest() != self.quote_sha256:
            raise ValueError('quote_sha256 does not match the exact quote')
        if self.source_sha256 is not None:
            _hash(self.source_sha256, 'source_sha256')
        if self.message_id is not None:
            object.__setattr__(self, 'message_id', required_text(self.message_id, 'message_id'))
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError('line positions must be a complete range')
        if self.line_start is not None and (
            type(self.line_start) is not int or type(self.line_end) is not int
            or self.line_start < 1 or self.line_end < self.line_start
        ):
            raise ValueError('invalid source line range')
        if self.message_id is None and self.line_start is None:
            raise ValueError('evidence needs a message identifier or source line range')
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError('character positions must be a complete range')
        if self.char_start is not None and (
            type(self.char_start) is not int or type(self.char_end) is not int
            or self.char_start < 0 or self.char_end - self.char_start != len(self.quote)
        ):
            raise ValueError('character range must match the exact quote length')
        if self.meeting_time is not None:
            object.__setattr__(self, 'meeting_time', utc_time(self.meeting_time, 'meeting_time'))

    def to_dict(self) -> dict[str, Any]:
        return dict(workspace_id=self.workspace_id, source_id=self.source_id,
                    source_version_id=self.source_version_id, quote=self.quote,
                    quote_sha256=self.quote_sha256, message_id=self.message_id,
                    line_start=self.line_start, line_end=self.line_end,
                    char_start=self.char_start, char_end=self.char_end,
                    source_sha256=self.source_sha256,
                    meeting_time=self.meeting_time.isoformat() if self.meeting_time else None)

    @classmethod
    def from_dict(cls, data: object) -> EvidenceReference:
        values = dict(_shape(data, set(cls.__dataclass_fields__), 'evidence'))
        if values['meeting_time'] is not None:
            values['meeting_time'] = _time_from_text(values['meeting_time'], 'meeting_time')
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TaskCandidateInput:
    """Boundary implemented by extractor adapters, without extractor-specific types.

    candidate_version identifies the adapter's candidate contract; changing a
    field or any ordered evidence segment changes the fingerprint. Source
    currentness and human authorization belong to the future review service.
    """

    candidate_id: str
    workspace_id: str
    title: str | None
    evidence: tuple[EvidenceReference, ...]
    owner: str | None = None
    deadline: str | None = None
    deadline_text: str | None = None
    meeting_time: datetime | None = None
    source_decision_id: str | None = None
    candidate_version: str = 'task-candidate-input/v1'
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in ('candidate_id', 'workspace_id', 'candidate_version'):
            object.__setattr__(self, name, required_text(getattr(self, name), name))
        for name in ('title', 'owner', 'deadline_text', 'source_decision_id'):
            object.__setattr__(self, name, optional_text(getattr(self, name), name))
        object.__setattr__(self, 'deadline', deadline_value(self.deadline))
        if self.source_fingerprint is not None:
            _hash(self.source_fingerprint, 'source_fingerprint')
        _evidence(self.evidence, self.workspace_id, required=True)
        if self.meeting_time is not None:
            object.__setattr__(self, 'meeting_time', utc_time(self.meeting_time, 'meeting_time'))

    def to_dict(self) -> dict[str, Any]:
        data = dict(candidate_id=self.candidate_id, workspace_id=self.workspace_id,
                    candidate_version=self.candidate_version, title=self.title, owner=self.owner,
                    deadline=self.deadline, deadline_text=self.deadline_text,
                    meeting_time=self.meeting_time.isoformat() if self.meeting_time else None,
                    source_decision_id=self.source_decision_id,
                    evidence=[item.to_dict() for item in self.evidence])
        if self.source_fingerprint is not None:
            data['source_fingerprint'] = self.source_fingerprint
        return data

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode('utf-8')).hexdigest()

    @classmethod
    def from_dict(cls, data: object) -> TaskCandidateInput:
        if isinstance(data, dict) and 'source_fingerprint' not in data:
            data = {**data, 'source_fingerprint': None}
        values = dict(_shape(data, set(cls.__dataclass_fields__), 'candidate input'))
        if not isinstance(values['evidence'], list):
            raise ValueError('evidence must be a JSON list')
        values['evidence'] = tuple(EvidenceReference.from_dict(item) for item in values['evidence'])
        if values['meeting_time'] is not None:
            values['meeting_time'] = _time_from_text(values['meeting_time'], 'meeting_time')
        return cls(**values)


def _evidence(values: object, workspace_id: str, *, required: bool) -> None:
    if (not isinstance(values, tuple) or (required and not values)
        or any(not isinstance(item, EvidenceReference) for item in values)):
        raise ValueError('evidence must be a tuple of source references')
    if any(item.workspace_id != workspace_id for item in values):
        raise ValueError('evidence belongs to another workspace')


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    workspace_id: str
    title: str
    owner: str | None
    deadline: str | None
    deadline_text: str | None
    status: TaskStatus
    source_decision_id: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ('id', 'workspace_id', 'title'):
            object.__setattr__(self, name, required_text(getattr(self, name), name))
        for name in ('owner', 'deadline_text', 'source_decision_id'):
            object.__setattr__(self, name, optional_text(getattr(self, name), name))
        object.__setattr__(self, 'deadline', deadline_value(self.deadline))
        object.__setattr__(self, 'status', TaskStatus(self.status))
        for name in ('created_at', 'updated_at'):
            object.__setattr__(self, name, utc_time(getattr(self, name), name))
        if self.updated_at < self.created_at:
            raise ValueError('updated_at cannot precede created_at')

    def to_dict(self) -> dict[str, Any]:
        return dict(id=self.id, workspace_id=self.workspace_id, title=self.title, owner=self.owner,
                    deadline=self.deadline, deadline_text=self.deadline_text, status=self.status.value,
                    source_decision_id=self.source_decision_id, created_at=self.created_at.isoformat(),
                    updated_at=self.updated_at.isoformat())

    @classmethod
    def from_dict(cls, data: object) -> Task:
        values = dict(_shape(data, set(cls.__dataclass_fields__), 'task'))
        for name in ('created_at', 'updated_at'):
            values[name] = _time_from_text(values[name], name)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TaskEvent:
    sequence: int
    id: str
    task_id: str
    workspace_id: str
    event_type: TaskEventType
    old_value: object
    new_value: object
    source_version_id: str | None
    evidence: tuple[EvidenceReference, ...]
    meeting_time: datetime | None
    actor: str
    reason: str
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError('event sequence must be a positive integer')
        for name in ('id', 'task_id', 'workspace_id', 'actor', 'reason'):
            object.__setattr__(self, name, required_text(getattr(self, name), name))
        object.__setattr__(self, 'event_type', TaskEventType(self.event_type))
        object.__setattr__(self, 'created_at', utc_time(self.created_at, 'created_at'))
        if self.meeting_time is not None:
            object.__setattr__(self, 'meeting_time', utc_time(self.meeting_time, 'meeting_time'))
        _evidence(self.evidence, self.workspace_id,
                  required=self.event_type is not TaskEventType.STATUS_CHANGED)
        versions = {item.source_version_id for item in self.evidence}
        expected_version = next(iter(versions)) if len(versions) == 1 else None
        if self.source_version_id != expected_version:
            raise ValueError('event version must describe all evidence segments or remain null')
        if self.event_type is TaskEventType.CREATED:
            initial = Task.from_dict(self.new_value)
            if self.old_value is not None or initial.id != self.task_id or initial.workspace_id != self.workspace_id:
                raise ValueError('created event must contain its own task snapshot')
            if initial.created_at != self.created_at or initial.updated_at != self.created_at:
                raise ValueError('created event timestamp must match the initial snapshot')
        elif self.event_type is TaskEventType.DEADLINE_CHANGED:
            for value in (self.old_value, self.new_value):
                fields = _shape(value, {'deadline', 'deadline_text'}, 'deadline change')
                deadline_value(fields['deadline'])
                optional_text(fields['deadline_text'], 'deadline_text')
        elif self.event_type is TaskEventType.STATUS_CHANGED:
            TaskStatus(self.old_value)
            TaskStatus(self.new_value)
        elif self.event_type is TaskEventType.TITLE_CHANGED:
            required_text(self.old_value, 'old title')
            required_text(self.new_value, 'new title')
        else:
            optional_text(self.old_value, 'old owner')
            optional_text(self.new_value, 'new owner')
        if self.event_type is not TaskEventType.CREATED and self.old_value == self.new_value:
            raise ValueError('change events must describe an actual change')

    def to_dict(self) -> dict[str, Any]:
        return dict(sequence=self.sequence, id=self.id, task_id=self.task_id,
                    workspace_id=self.workspace_id, event_type=self.event_type.value,
                    old_value=self.old_value, new_value=self.new_value,
                    source_version_id=self.source_version_id,
                    evidence=[item.to_dict() for item in self.evidence],
                    meeting_time=self.meeting_time.isoformat() if self.meeting_time else None,
                    actor=self.actor, reason=self.reason, created_at=self.created_at.isoformat())


@dataclass(frozen=True, slots=True)
class ObservationLink:
    observation_id: str
    workspace_id: str
    task_id: str | None
    review_action: ReviewAction
    reviewer: str
    reason: str
    reviewed_at: datetime
    candidate_fingerprint: str

    def __post_init__(self) -> None:
        for name in ('observation_id', 'workspace_id', 'reviewer', 'reason'):
            object.__setattr__(self, name, required_text(getattr(self, name), name))
        object.__setattr__(self, 'review_action', ReviewAction(self.review_action))
        object.__setattr__(self, 'task_id', optional_text(self.task_id, 'task_id'))
        object.__setattr__(self, 'reviewed_at', utc_time(self.reviewed_at, 'reviewed_at'))
        _hash(self.candidate_fingerprint, 'candidate_fingerprint')
        if (self.review_action is ReviewAction.REJECT) != (self.task_id is None):
            raise ValueError('only rejected candidates have no task link')

    def to_dict(self) -> dict[str, Any]:
        return dict(observation_id=self.observation_id, workspace_id=self.workspace_id,
                    task_id=self.task_id, review_action=self.review_action.value,
                    reviewer=self.reviewer, reason=self.reason, reviewed_at=self.reviewed_at.isoformat(),
                    candidate_fingerprint=self.candidate_fingerprint)
