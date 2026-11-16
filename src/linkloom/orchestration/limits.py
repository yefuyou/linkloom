"""Host-owned limits of one orchestration run.

The Supervisor model only proposes tasks. Task count, concurrency, depth, rounds, attempts, deadline and
Provider budget are decided here, deterministically. Provider cost accounting stays in TaskProviderSession;
this module only decides how many sessions the total budget can pay for.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from linkloom.ui.task_provider import TaskProviderLimits

_BUDGET_KEYS = ('max_requests', 'max_input_tokens', 'max_output_tokens', 'max_cost_usd')


@dataclass(frozen=True)
class OrchestrationLimits:
    provider: TaskProviderLimits     # the whole run
    per_task: TaskProviderLimits     # one Worker attempt
    supervisor: TaskProviderLimits   # one Supervisor decision
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

    def supervisor_reserve(self):
        """Every Supervisor decision the run may take: the first plan plus max_rounds reviews."""
        return {key: getattr(self.supervisor, key) * (self.max_rounds + 1) for key in _BUDGET_KEYS}

    def affordable_dispatches(self):
        """How many Worker attempts the total budget can pay for after the Supervisor reserve."""
        reserve = self.supervisor_reserve()
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
        """Equal slices of one total budget: max_tasks Workers and max_rounds + 1 Supervisor decisions."""
        slots = max_tasks + max_rounds + 1

        def share(count):
            data = {key: getattr(provider, key) / slots * count for key in _BUDGET_KEYS}
            return replace(provider, max_requests=max(1, int(data['max_requests'])),
                           max_input_tokens=max(1, int(data['max_input_tokens'])),
                           max_output_tokens=max(1, int(data['max_output_tokens'])),
                           max_cost_usd=data['max_cost_usd'])
        return cls(provider=provider, per_task=share(1), supervisor=share(1),
                   max_tasks=max_tasks, max_rounds=max_rounds, **options)
