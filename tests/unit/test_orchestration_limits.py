from dataclasses import replace

import pytest

from linkloom.orchestration import OrchestrationLimits
from linkloom.ui.task_provider import TaskProviderLimits


# Run budget after the Router reservation in the #114 Q3 acceptance trace.
RUN = TaskProviderLimits(20, 391_482, 57_952, .3359315, 177, 30, .01, .75, 3.75)


def test_tight_cost_reduces_workers_and_reviews_within_the_run_budget():
    limits = OrchestrationLimits.for_budget(RUN)
    assert (limits.task_cap(), limits.max_rounds) == (1, 0)
    assert limits.per_task.max_cost_usd == pytest.approx(.16796575)
    for field in ("max_requests", "max_input_tokens", "max_output_tokens", "max_cost_usd"):
        workers = limits.dispatch_cap() * getattr(limits.per_task, field)
        assert limits.supervisor_reserve()[field] + workers <= getattr(RUN, field)
    assert limits.deadline_seconds == RUN.max_seconds


@pytest.mark.parametrize(("field", "value"), [
    ("max_requests", 7),
    ("max_input_tokens", 200_000),
    ("max_output_tokens", 8190),
    ("max_cost_usd", .08),
    ("request_cost_upper_bound_usd", .05),
])
def test_allocation_rejects_when_even_one_worker_and_supervisor_do_not_fit(field, value):
    with pytest.raises(ValueError, match="TASK_PROVIDER_BUDGET_EXHAUSTED"):
        OrchestrationLimits.for_budget(replace(RUN, **{field: value}))
