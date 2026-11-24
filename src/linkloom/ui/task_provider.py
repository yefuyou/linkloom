"""Per-question Provider journal at the existing complete(request) seam.

No planning, tools, business approvals or model retries live here. A failed or
uncertain attempt terminates the question, including other task stages. The
session records what the Provider actually reported; it never limits spend.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, UTC
import json
import math
import os
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from linkloom.agents.model_adapter import ModelProviderError, ModelResponse
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.lifecycle import BackgroundTasks, DEFAULT_SHUTDOWN_TIMEOUT, shutdown_deadline

JOURNAL_NAME = 'provider_journal.json'
LEGACY_JOURNAL_NAME = 'budget.json'
JOURNAL_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class TaskProviderLimits:
    """Wall-clock bounds of one Provider session. Cost and token usage are observed, never limited."""
    max_seconds: float
    request_timeout_seconds: float

    def __post_init__(self):
        for key in ('max_seconds', 'request_timeout_seconds'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('positive finite task duration required: ' + key)
        if self.request_timeout_seconds > self.max_seconds:
            raise ValueError('task request timeout exceeds total duration')

    @classmethod
    def from_dict(cls, data):
        """Build limits from a stored dict; keys of the removed budget fields are ignored."""
        return cls(**{field.name: data[field.name] for field in fields(cls) if field.name in data})


_ROUTER_VALIDATION = ContextVar("ask_router_source_validation", default=None)


@contextmanager
def pass_router_source_validation(session):
    """Hand the Router session's source-authorization check to the investigation that follows it.

    Only the validate() callable travels. Nothing the Router session recorded (usage, time, requests) is
    visible to or charged against the investigation session.
    """
    token = _ROUTER_VALIDATION.set(None if session is None else session.validate)
    try:
        yield
    finally:
        _ROUTER_VALIDATION.reset(token)


def read_provider_journal(directory):
    """Return the Provider journal of a session directory, or None.

    Reads the current provider_journal.json and, read-only, the legacy budget.json of historical runs.
    """
    for name in (JOURNAL_NAME, LEGACY_JOURNAL_NAME):
        try:
            data = json.loads((Path(directory) / name).read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def journal_request_count(journal):
    """Number of Provider requests a journal says were sent (current and legacy field names)."""
    count = journal.get('requests', journal.get('requests_reserved'))
    return count if type(count) is int else 0


class TaskProviderSession:
    def __init__(self, root, limits, validate):
        self.root, self.limits, self.validate = Path(root), limits, validate
        self.root.mkdir(parents=True, exist_ok=True)
        self.started = monotonic()
        self.started_at = datetime.now(UTC).isoformat()
        self.failure = None
        self.requests = 0
        self.attempts = []
        self.lock = Lock()
        self._workers = BackgroundTasks()
        self._calls = BackgroundTasks()
        self._save()

    def pending_task_ids(self):
        return (*self._calls.pending_ids(), *self._workers.pending_ids())

    def close(self, timeout=DEFAULT_SHUTDOWN_TIMEOUT, *, deadline=None):
        deadline = shutdown_deadline(timeout) if deadline is None else deadline
        self._calls.stop_accepting()
        self._calls.wait(deadline)
        self._workers.stop_accepting()
        self._workers.wait(deadline)

    def bind(self, adapter, stage):
        return _JournaledTaskAdapter(self, adapter, stage)

    def record_validation(self, record):
        fields = {key: record[key] for key in ("provider_reply", "invalid_response_counts") if key in record}
        if fields:
            with self.lock:
                if self.attempts:
                    self.attempts[-1].update(fields)
                    self._save()

    def _save(self):
        # Fsync the journal before crossing the transport so a crash leaves a PENDING row behind.
        # No credentials, original quotes or provider continuation are copied into this journal.
        with (self.root / JOURNAL_NAME).open('w', encoding='utf8') as stream:
            json.dump(dict(schema_version=JOURNAL_SCHEMA_VERSION, limits=asdict(self.limits),
                requests=self.requests, started_at=self.started_at,
                elapsed_seconds=round(monotonic() - self.started, 3), failure=self.failure,
                attempts=self.attempts), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    def _error(self, reason, *, uncertain=False):
        self.failure = reason
        self._save()
        return ModelResponse(error=ModelProviderError(
            code='MODEL_UNKNOWN_FAILURE' if uncertain else 'MODEL_CANCELLED',
            category='unknown_provider_error' if uncertain else 'cancelled',
            message='Task Provider execution stopped.', retryable=False,
            outcome='unknown_provider_outcome' if uncertain else 'known_failure',
            details=dict(reason=reason)))

    def complete(self, adapter, stage, request):
        with self._calls.operation("task-provider-call-" + uuid4().hex[:12]):
            return self._complete(adapter, stage, request)

    def _complete(self, adapter, stage, request):
        with self.lock:
            if self.failure:
                return self._error(self.failure)
            try:
                self.validate()
            except Exception:
                return self._error('TASK_SOURCE_AUTHORIZATION_CHANGED')
            remaining = self.limits.max_seconds - (monotonic() - self.started)
            if remaining <= 0:
                return self._error('TASK_PROVIDER_DEADLINE')
            self.requests += 1
            row = dict(sequence=self.requests, stage=stage, request_sha256=canonical_hash(request.to_dict()),
                outcome='PENDING')
            self.attempts.append(row)
            self._save()
            done, result = Event(), []

            def invoke():
                try:
                    result.append(adapter.complete(request))
                except Exception:
                    result.append(None)
                finally:
                    done.set()

            self._workers.start(f'task-provider-{self.root.parent.name}-{self.requests}', invoke)
            if not done.wait(min(remaining, self.limits.request_timeout_seconds)):
                row['outcome'] = 'UNCERTAIN'
                return self._error('PROVIDER_TIMEOUT_UNCERTAIN', uncertain=True)
            response = result[0] if result else None
            if not isinstance(response, ModelResponse):
                row['outcome'] = 'UNCERTAIN'
                return self._error('PROVIDER_UNCERTAIN', uncertain=True)
            row.update(response_sha256=canonical_hash(response.to_dict()), usage=response.usage.to_dict(),
                provider_request_id=response.provider_request_id, provider_response_id=response.provider_response_id)
            if response.error:
                uncertain = (response.error.outcome == 'unknown_provider_outcome'
                             or response.error.code == 'MODEL_TIMEOUT')
                row.update(outcome='UNCERTAIN' if uncertain else 'FAILED', code=response.error.code,
                           error=response.error.to_dict())
                failure = self._error('PROVIDER_UNCERTAIN' if uncertain else response.error.code, uncertain=uncertain)
                if response.finish_reason == 'length':
                    return replace(failure, finish_reason=response.finish_reason, usage=response.usage,
                                   provider_metadata=response.provider_metadata)
                return failure
            try:
                self.validate()
            except Exception:
                row['outcome'] = 'WITHHELD'
                return self._error('TASK_SOURCE_AUTHORIZATION_CHANGED')
            if monotonic() - self.started >= self.limits.max_seconds:
                row['outcome'] = 'WITHHELD'
                return self._error('TASK_PROVIDER_DEADLINE')
            row['outcome'] = 'SUCCEEDED'
            self._save()
            return response


class _JournaledTaskAdapter:
    def __init__(self, session, adapter, stage):
        self.session, self.adapter, self.stage = session, adapter, stage
        self.capability = getattr(adapter, 'capability', None)

    def complete(self, request):
        return self.session.complete(self.adapter, self.stage, request)


def validate_task_provider_receipt(path, completed_at):
    """Require a durable, structurally complete journal of successful requests before releasing a LIVE result."""
    journal = json.loads(Path(path).read_text(encoding='utf8'))
    if journal['failure'] is not None or not journal['attempts']:
        raise ValueError('TASK_PROVIDER_RECEIPT_INCOMPLETE')
    _validate_provider_journal(journal, completed_at, require_success=True)


def _validate_provider_journal(journal, completed_at, *, require_success=False):
    limits = TaskProviderLimits.from_dict(journal['limits'])
    attempts = journal['attempts']
    if (not isinstance(attempts, list) or journal_request_count(journal) != len(attempts)):
        raise ValueError('TASK_PROVIDER_RECEIPT_INCOMPLETE')
    for sequence, row in enumerate(attempts, 1):
        outcome = row['outcome']
        if (outcome not in ('SUCCEEDED', 'FAILED', 'PENDING', 'UNCERTAIN', 'WITHHELD')
                or (require_success and outcome != 'SUCCEEDED') or type(row['sequence']) is not int
                or row['sequence'] != sequence
                or not isinstance(row['stage'], str) or not row['stage']
                or any(not isinstance(row[key], str) or len(row[key]) != 64
                       or any(c not in '0123456789abcdef' for c in row[key])
                       for key in (('request_sha256', 'response_sha256') if outcome == 'SUCCEEDED'
                                   else ('request_sha256',)))):
            raise ValueError('TASK_PROVIDER_RECEIPT_INCOMPLETE')
    elapsed = (datetime.fromisoformat(completed_at.replace('Z', '+00:00')) -
               datetime.fromisoformat(journal['started_at'])).total_seconds()
    measured = journal['elapsed_seconds']
    # Runtime checkpoints use whole seconds; a subsecond run can therefore have
    # a truncated completion stamp just before its precise start stamp.
    if (elapsed < -1 or elapsed > limits.max_seconds
            or isinstance(measured, bool) or not isinstance(measured, (int, float))
            or not math.isfinite(measured) or not 0 <= measured <= limits.max_seconds):
        raise ValueError('TASK_PROVIDER_DURATION_EXCEEDED')


def compose_task_provider(app, provider, limits, *, enable_temporal=False, parallel_investigation=False):
    """Host configuration enables task stages; Ask still needs a source grant."""
    from linkloom.agents.providers import GeminiProviderAdapter
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.ui.task_investigation import TaskInvestigationModels
    if not isinstance(provider, GeminiProviderAdapter) or not isinstance(limits, TaskProviderLimits):
        raise ValueError('explicit Gemini adapter and task provider limits required')
    models = TaskInvestigationModels(provider, provider, provider,
        execution_kind='LIVE', provider_id='gemini', model_id=provider.model_id, provider_limits=limits)
    return ProductInvestigationService(app, model_factory=lambda _question: provider,
        task_models=models, external_provider=True, run_mode='GEMINI_TASK_EXPLICIT',
        task_only=not enable_temporal, parallel_limits=limits if parallel_investigation else None)
