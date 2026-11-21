"""Host-owned limits of one orchestration run.

The Supervisor model only proposes tasks. Task count, concurrency, depth, rounds, attempts, deadline and
Provider budget are decided here, deterministically. Provider cost accounting stays in TaskProviderSession;
this module only decides how many sessions the total budget can pay for.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import math

from linkloom.agents.model_adapter import MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES
from linkloom.runtime.models import RunRequest
from linkloom.orchestration.supervisor import SUPERVISOR_OUTPUT_TOKENS
from linkloom.ui.task_provider import TaskProviderLimits

_BUDGET_KEYS = ('max_requests', 'max_input_tokens', 'max_output_tokens', 'max_cost_usd')


@dataclass(frozen=True)
class OrchestrationLimits:
    provider: TaskProviderLimits     # the whole run
    per_task: TaskProviderLimits     # one Worker attempt
    supervisor: TaskProviderLimits   # one Supervisor decision
    comparison: TaskProviderLimits | None = None  # the one bounded claim comparison; None = not budgeted
    max_tasks: int = 6
    max_concurrency: int = 3
    max_depth: int = 3
    max_rounds: int = 2              # Supervisor reviews after the first plan
    max_attempts: int = 1            # attempts per task; a retry is a new paid attempt
    deadline_seconds: float = 120.0
    confirm_over_tasks: int | None = None  # a plan with more new tasks waits for human confirmation

    def __post_init__(self):
        for key in ('max_tasks', 'max_concurrency', 'max_depth', 'max_attempts'):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError('positive integer orchestration limit required: ' + key)
        if type(self.max_rounds) is not int or self.max_rounds < 0:
            raise ValueError('non-negative integer orchestration limit required: max_rounds')
        if (isinstance(self.deadline_seconds, bool) or not isinstance(self.deadline_seconds, (int, float))
                or not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0):
            raise ValueError('positive finite orchestration limit required: deadline_seconds')
        if self.confirm_over_tasks is not None and (
                type(self.confirm_over_tasks) is not int or self.confirm_over_tasks < 0):
            raise ValueError('non-negative integer orchestration limit required: confirm_over_tasks')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        for key in ('provider', 'per_task', 'supervisor'):
            data[key] = TaskProviderLimits(**data[key])
        data['comparison'] = TaskProviderLimits(**data['comparison']) if data.get('comparison') else None
        return cls(**{field.name: data[field.name] for field in fields(cls) if field.name in data})

    def supervisor_reserve(self):
        """Every Supervisor decision the run may take: the first plan plus max_rounds reviews."""
        return {key: getattr(self.supervisor, key) * (self.max_rounds + 1) for key in _BUDGET_KEYS}

    def comparison_reserve(self):
        """The single claim-comparison request, held back from the Worker budget so it can always be paid."""
        return {key: getattr(self.comparison, key) if self.comparison else 0 for key in _BUDGET_KEYS}

    def affordable_dispatches(self):
        """How many Worker attempts the total budget can pay for after the Supervisor and comparison reserve."""
        reserve = {key: value + self.comparison_reserve()[key] for key, value in self.supervisor_reserve().items()}
        counts = [max(0, math.floor((getattr(self.provider, key) - reserve[key]) / getattr(self.per_task, key) + 1e-9))
                  for key in _BUDGET_KEYS]
        return min(counts)

    def dispatch_cap(self):
        """Hard ceiling of paid Worker attempts for the run (tasks, retries and the budget)."""
        return min(self.max_tasks * self.max_attempts, self.affordable_dispatches())

    def task_cap(self):
        return min(self.max_tasks, self.affordable_dispatches())

    @classmethod
    def split(cls, provider, *, max_tasks=6, max_rounds=2, **options):
        """Equal slices of one total budget: max_tasks Workers, max_rounds + 1 Supervisor decisions and, when
        two or more answers can exist, one claim comparison."""
        slots = max_tasks + max_rounds + 1 + (1 if max_tasks > 1 else 0)

        def share(count):
            data = {key: getattr(provider, key) / slots * count for key in _BUDGET_KEYS}
            return replace(provider, max_requests=max(1, int(data['max_requests'])),
                           max_input_tokens=max(1, int(data['max_input_tokens'])),
                           max_output_tokens=max(1, int(data['max_output_tokens'])),
                           max_cost_usd=data['max_cost_usd'])
        return cls(provider=provider, per_task=share(1), supervisor=share(1),
                   comparison=share(1) if max_tasks > 1 else None, max_tasks=max_tasks, max_rounds=max_rounds, **options)

    WORKER_MIN_REQUESTS = 4  # search, read, one more evidence turn and the reserved Final turn
    WORKER_TARGET_REQUESTS = 6  # prefer room for follow-up search/read before spending slices on more tasks
    WORKER_MAX_STEPS = RunRequest.__dataclass_fields__['max_steps'].default
    WORKER_MAX_OUTPUT_PER_REQUEST = 4096
    WORKER_MIN_OUTPUT_PER_REQUEST = 1024  # keep space for a complete, cited Final when adding turns
    # A planning allowance per turn for an evidence-sized request plus framing.
    # Tool history and other fields can make the serialized request larger.
    WORKER_INPUT_ALLOWANCE = MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES + 4096

    @classmethod
    def worker_turn_budget(cls, budget):
        """Finite Runtime turns including Final, with the same nominal allowance used by planning.

        Actual serialized input, elapsed time and costs remain enforced by TaskProviderSession.
        A Worker may finish early; this is a ceiling, never a required number of calls.
        """
        ceiling = min(cls.WORKER_MAX_STEPS, budget.max_requests,
                      budget.max_input_tokens // cls.WORKER_INPUT_ALLOWANCE,
                      budget.max_output_tokens // cls.WORKER_MIN_OUTPUT_PER_REQUEST)
        for turns in range(ceiling, cls.WORKER_MIN_REQUESTS - 1, -1):
            input_cost = cls.WORKER_INPUT_ALLOWANCE * budget.input_usd_per_million / 1_000_000
            affordable_output = math.floor((budget.max_cost_usd / turns - input_cost)
                                           * 1_000_000 / budget.output_usd_per_million + 1e-8)
            output = min(cls.WORKER_MAX_OUTPUT_PER_REQUEST, budget.max_output_tokens // turns, affordable_output)
            if (output >= cls.WORKER_MIN_OUTPUT_PER_REQUEST
                    and turns * budget.reservation_cost(cls.WORKER_INPUT_ALLOWANCE, output) <= budget.max_cost_usd + 1e-10):
                return turns, output
        return 0, 0

    @classmethod
    def _fits_worker_turns(cls, budget, minimum=None):
        turns, _ = cls.worker_turn_budget(budget)
        return (budget.max_output_tokens >= SUPERVISOR_OUTPUT_TOKENS
                and turns >= (minimum or cls.WORKER_MIN_REQUESTS))

    @classmethod
    def for_budget(cls, provider, *, max_tasks=6, max_rounds=2, **options):
        """Prefer follow-up research space before more equal Worker slices; retain the four-turn minimum.

        Actual serialized requests and elapsed time stay subject to the Provider session.
        Raises ValueError when no allocation meets the planning allowance.
        """
        for minimum in (cls.WORKER_TARGET_REQUESTS, cls.WORKER_MIN_REQUESTS):
            for tasks in range(max_tasks, 0, -1):
                for rounds in range(min(max_rounds, tasks), -1, -1):
                    if rounds == 0 and tasks > 1:
                        continue  # delegation needs room for a Supervisor review
                    limits = cls.split(provider, max_tasks=tasks, max_rounds=rounds,
                                       deadline_seconds=float(provider.max_seconds),
                                       **dict(dict(max_concurrency=min(3, tasks)), **options))
                    if cls._fits_worker_turns(limits.per_task, minimum):
                        return limits
        raise ValueError('TASK_PROVIDER_BUDGET_EXHAUSTED')
