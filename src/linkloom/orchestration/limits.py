"""Host-owned limits of one orchestration run.

The Supervisor model only proposes tasks. Task count, concurrency, depth, rounds, attempts, steps and deadline
are decided here, deterministically. These are run-level stop conditions, not a money allocation: there is no
per-role dollar, token or request slice. Every Provider session (Supervisor, Worker, comparison) runs under the
same time limits (total duration, request timeout) and records the usage it observed in its own
provider_journal.json. Cost and token usage are observed, never used to stop a run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math

from linkloom.ui.task_provider import TaskProviderLimits


@dataclass(frozen=True)
class OrchestrationLimits:
    provider: TaskProviderLimits     # Provider time limits shared by every session of the run
    max_tasks: int = 6               # tasks of the first plan
    max_followup_tasks: int = 2      # extra tasks a review round may add on top of the first plan
    max_concurrency: int = 3
    max_depth: int = 3
    max_rounds: int = 2              # Supervisor reviews after the first plan
    max_attempts: int = 1            # attempts per task; a retry is a new attempt
    max_steps: int = 8               # model turns of one Worker; the last one is reserved for the final answer
    max_request_output_tokens: int = 4096  # per-request output cap of a Worker
    deadline_seconds: float = 120.0
    confirm_over_tasks: int | None = None  # a plan with more new tasks waits for human confirmation

    def __post_init__(self):
        for key in ('max_tasks', 'max_concurrency', 'max_depth', 'max_attempts', 'max_steps',
                    'max_request_output_tokens'):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError('positive integer orchestration limit required: ' + key)
        for key in ('max_rounds', 'max_followup_tasks'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError('non-negative integer orchestration limit required: ' + key)
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
        data['provider'] = TaskProviderLimits.from_dict(data['provider'])
        return cls(**{field.name: data[field.name] for field in fields(cls) if field.name in data})

    def task_cap(self):
        """Most tasks the run may hold: the first plan plus the follow-ups a review may add."""
        return self.max_tasks + self.max_followup_tasks

    def dispatch_cap(self):
        """Ceiling of Worker attempts for the run (tasks and retries)."""
        return self.task_cap() * self.max_attempts
