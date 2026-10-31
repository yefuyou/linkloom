"""Per-question Provider reservations at the existing complete(request) seam.

No planning, tools, business approvals or model retries live here. A failed or
uncertain attempt terminates the question, including other task stages.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, UTC
import json
import math
import os
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from linkloom.agents.model_adapter import ModelProviderError, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.lifecycle import BackgroundTasks, DEFAULT_SHUTDOWN_TIMEOUT, shutdown_deadline


@dataclass(frozen=True)
class TaskProviderLimits:
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: float
    max_seconds: float
    request_timeout_seconds: float
    request_cost_upper_bound_usd: float
    input_usd_per_million: float = 1.0
    output_usd_per_million: float = 5.0

    def __post_init__(self):
        for key in ('max_requests', 'max_input_tokens', 'max_output_tokens'):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError('positive integer task budget required: ' + key)
        for key in ('max_cost_usd', 'max_seconds', 'request_timeout_seconds', 'request_cost_upper_bound_usd',
                    'input_usd_per_million', 'output_usd_per_million'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('positive finite task budget required: ' + key)
        if self.request_timeout_seconds > self.max_seconds:
            raise ValueError('task request timeout exceeds total duration budget')


class TaskProviderSession:
    def __init__(self, root, limits, validate):
        self.root, self.limits, self.validate = Path(root), limits, validate
        self.root.mkdir(parents=True, exist_ok=True)
        self.started = monotonic()
        self.started_at = datetime.now(UTC).isoformat()
        self.failure = None
        self.requests = self.input_tokens = self.output_tokens = 0
        self.cost = 0.0
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
        return _BudgetedTaskAdapter(self, adapter, stage)

    def _save(self):
        # Fsync reservations before crossing the transport. No credentials,
        # original quotes or provider continuation are copied into this journal.
        with (self.root / 'budget.json').open('w', encoding='utf8') as stream:
            json.dump(dict(limits=asdict(self.limits), requests_reserved=self.requests,
                started_at=self.started_at,
                input_tokens_reserved=self.input_tokens, output_tokens_reserved=self.output_tokens,
                cost_reserved_usd=round(self.cost, 8), actual_cost_usd=None,
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
            # One byte per token plus conservative framing/schema allowance.
            size = len(json.dumps(request.to_dict(), ensure_ascii=False).encode('utf8')) + 4096
            output = request.generation_options.max_output_tokens
            cost = max(self.limits.request_cost_upper_bound_usd,
                (size * self.limits.input_usd_per_million + output * self.limits.output_usd_per_million) / 1_000_000)
            if (self.requests >= self.limits.max_requests
                    or self.input_tokens + size > self.limits.max_input_tokens
                    or self.output_tokens + output > self.limits.max_output_tokens
                    or self.cost + cost > self.limits.max_cost_usd + 1e-10 or remaining <= 0):
                return self._error('TASK_PROVIDER_BUDGET_EXHAUSTED')
            self.requests += 1
            self.input_tokens += size
            self.output_tokens += output
            self.cost += cost
            row = dict(sequence=self.requests, stage=stage, request_sha256=canonical_hash(request.to_dict()),
                input_reserved=size, output_reserved=output, cost_reserved_usd=cost, outcome='PENDING')
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
                return self._error('PROVIDER_UNCERTAIN' if uncertain else response.error.code, uncertain=uncertain)
            if ((response.usage.input_tokens or 0) > size or (response.usage.output_tokens or 0) > output
                    or (response.usage.total_tokens or 0) > size + output):
                row['outcome'] = 'FAILED'
                return self._error('PROVIDER_USAGE_EXCEEDED')
            try:
                self.validate()
            except Exception:
                row['outcome'] = 'WITHHELD'
                return self._error('TASK_SOURCE_AUTHORIZATION_CHANGED')
            if monotonic() - self.started >= self.limits.max_seconds:
                row['outcome'] = 'WITHHELD'
                return self._error('TASK_PROVIDER_BUDGET_EXHAUSTED')
            row['outcome'] = 'SUCCEEDED'
            self._save()
            return response


class _BudgetedTaskAdapter:
    def __init__(self, session, adapter, stage):
        self.session, self.adapter, self.stage = session, adapter, stage
        self.capability = getattr(adapter, 'capability', None)

    def complete(self, request):
        return self.session.complete(self.adapter, self.stage, request)


def validate_task_provider_receipt(path, completed_at):
    """Require durable successful reservations before releasing a LIVE result."""
    budget = json.loads(Path(path).read_text(encoding='utf8'))
    limits = TaskProviderLimits(**budget['limits'])
    attempts = budget['attempts']
    if (budget['failure'] is not None or not isinstance(attempts, list) or not attempts
            or type(budget['requests_reserved']) is not int
            or budget['requests_reserved'] != len(attempts) or len(attempts) > limits.max_requests):
        raise ValueError('TASK_PROVIDER_RECEIPT_INCOMPLETE')
    input_reserved = output_reserved = 0
    cost_reserved = 0.0
    for sequence, row in enumerate(attempts, 1):
        if (row['outcome'] != 'SUCCEEDED' or type(row['sequence']) is not int
                or row['sequence'] != sequence
                or any(type(row[key]) is not int or row[key] < 1
                       for key in ('input_reserved', 'output_reserved'))
                or not isinstance(row['stage'], str) or not row['stage']
                or any(not isinstance(row[key], str) or len(row[key]) != 64
                       or any(c not in '0123456789abcdef' for c in row[key])
                       for key in ('request_sha256', 'response_sha256'))):
            raise ValueError('TASK_PROVIDER_RECEIPT_INCOMPLETE')
        cost = row['cost_reserved_usd']
        minimum_cost = max(limits.request_cost_upper_bound_usd,
            (row['input_reserved'] * limits.input_usd_per_million
             + row['output_reserved'] * limits.output_usd_per_million) / 1_000_000)
        if (isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost + 1e-10 < minimum_cost):
            raise ValueError('TASK_PROVIDER_RECEIPT_BUDGET_INVALID')
        usage = ModelUsage.from_dict(row['usage'])
        if ((usage.input_tokens or 0) > row['input_reserved']
                or (usage.output_tokens or 0) > row['output_reserved']
                or (usage.total_tokens or 0) > row['input_reserved'] + row['output_reserved']):
            raise ValueError('TASK_PROVIDER_RECEIPT_USAGE_INVALID')
        input_reserved += row['input_reserved']
        output_reserved += row['output_reserved']
        cost_reserved += cost
    if (type(budget['input_tokens_reserved']) is not int
            or type(budget['output_tokens_reserved']) is not int
            or budget['input_tokens_reserved'] != input_reserved
            or budget['output_tokens_reserved'] != output_reserved
            or input_reserved > limits.max_input_tokens or output_reserved > limits.max_output_tokens
            or isinstance(budget['cost_reserved_usd'], bool)
            or not isinstance(budget['cost_reserved_usd'], (int, float))
            or not math.isclose(budget['cost_reserved_usd'], cost_reserved, rel_tol=0, abs_tol=1e-8)
            or cost_reserved > limits.max_cost_usd + 1e-10):
        raise ValueError('TASK_PROVIDER_RECEIPT_BUDGET_INVALID')
    elapsed = (datetime.fromisoformat(completed_at.replace('Z', '+00:00')) -
               datetime.fromisoformat(budget['started_at'])).total_seconds()
    measured = budget['elapsed_seconds']
    # Runtime checkpoints use whole seconds; a subsecond run can therefore have
    # a truncated completion stamp just before its precise start stamp.
    if (elapsed < -1 or elapsed > limits.max_seconds
            or isinstance(measured, bool) or not isinstance(measured, (int, float))
            or not math.isfinite(measured) or not 0 <= measured <= limits.max_seconds):
        raise ValueError('TASK_PROVIDER_DURATION_EXCEEDED')


def compose_task_provider(app, provider, limits, *, enable_temporal=False):
    """Host configuration enables task stages; Ask still needs a source grant."""
    from linkloom.agents.providers import GeminiProviderAdapter
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.ui.task_investigation import TaskInvestigationModels
    if not isinstance(provider, GeminiProviderAdapter) or not isinstance(limits, TaskProviderLimits):
        raise ValueError('explicit Gemini adapter and task budgets required')
    models = TaskInvestigationModels(provider, provider, provider,
        execution_kind='LIVE', provider_id='gemini', model_id=provider.model_id, provider_limits=limits)
    return ProductInvestigationService(app, model_factory=lambda _question: provider,
        task_models=models, external_provider=True, run_mode='GEMINI_TASK_EXPLICIT', task_only=not enable_temporal)
