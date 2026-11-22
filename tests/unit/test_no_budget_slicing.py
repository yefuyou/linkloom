"""The default product path has no per-role budget slices and no budget-driven Final-only."""
from pathlib import Path
import re

from linkloom.orchestration import OrchestrationLimits
from linkloom.orchestration.worker import run_cost_usd
from linkloom.ui.task_provider import TaskProviderLimits

SRC = Path(__file__).resolve().parents[2] / "src" / "linkloom"
REMOVED = re.compile(r"worker_turn_budget|worker_limits|released_supervisor_share|WORKER_FINAL_INPUT_UPPER_BOUND"
                     r"|WORKER_INPUT_ALLOWANCE|WORKER_MIN_REQUESTS|WORKER_MAX_OUTPUT_PER_REQUEST|_fits_worker_turns"
                     r"|OrchestrationLimits\.split|for_budget|supervisor_reserve|comparison_reserve"
                     r"|affordable_dispatches")


def test_no_source_file_references_a_removed_slicing_mechanism():
    hits = [f"{path.relative_to(SRC)}:{number}" for path in SRC.rglob("*.py")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if REMOVED.search(line)]
    assert hits == []


def test_limits_carry_no_per_role_budget_fields():
    names = set(OrchestrationLimits.__dataclass_fields__)
    assert not names & {"per_task", "supervisor", "comparison"}
    assert not any(hasattr(OrchestrationLimits, name) for name in ("split", "for_budget"))
    limits = OrchestrationLimits(provider=TaskProviderLimits(20, 1_000_000, 50_000, 1.0, 60, 10, .01))
    assert limits.max_run_cost_usd is None  # the whole-run cost fuse is opt-in


def test_only_the_last_step_forces_final_only_in_the_model_loop():
    text = (SRC / "runtime" / "model_loop.py").read_text(encoding="utf-8")
    assignments = [line.strip() for line in text.splitlines() if re.match(r"\s*final_only = ", line)]
    assert assignments == ['final_only = recovery_decision.action in {"FINAL_CONFLICT", "FINAL_PARTIAL"}',
                           "final_only = True"]
    assert "self.reserve_final_turn and model_steps == self.max_steps" in text
    assert not re.search(r"tool_window|final_only\s*=.*(budget|cost)", text)


def test_run_cost_totals_every_session_receipt(tmp_path):
    for name, cost in (("a", .25), ("b", .5)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "budget.json").write_text(f'{{"cost_reserved_usd": {cost}}}', encoding="utf-8")
    assert run_cost_usd(tmp_path) == .75
