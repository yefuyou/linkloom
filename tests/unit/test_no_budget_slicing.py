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
    limits = OrchestrationLimits(provider=TaskProviderLimits(60, 10))
    assert limits.max_run_cost_usd is None  # the whole-run cost fuse is opt-in


def test_only_the_last_step_forces_final_only_in_the_model_loop():
    text = (SRC / "runtime" / "model_loop.py").read_text(encoding="utf-8")
    assignments = [line.strip() for line in text.splitlines() if re.match(r"\s*final_only = ", line)]
    assert assignments == ['final_only = recovery_decision.action == "FINAL_PARTIAL"',
                           "final_only = True"]
    assert "self.reserve_final_turn and model_steps == self.max_steps" in text
    assert not re.search(r"tool_window|final_only\s*=.*(budget|cost)", text)


def test_run_cost_totals_every_session_receipt(tmp_path):
    for name, cost in (("a", .25), ("b", .5)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "budget.json").write_text(f'{{"cost_reserved_usd": {cost}}}', encoding="utf-8")
    assert run_cost_usd(tmp_path) == .75


# Names are split so this file never matches its own scan.
REMOVED_NL_GATES = re.compile("|".join(["plan_team_decision" + "_query", "plan_relation" + "_investigation",
                                        "_RELATION" + "_QUESTION", "_STATE" + "_FORM", "team_decision" + "_planner"]))
# Natural-language relation vocabulary must not return as a table or pattern in production code.
NL_RELATION_WORDS = re.compile(r"""re\.(?:compile|search|match|fullmatch|findall)\([^)\n]*"""
                               r"""(?:直属|汇报|负责人|accountable owner|reports? (?:directly )?to|direct (?:leader|manager))""")


def test_no_production_module_keeps_a_natural_language_gate_or_relation_regex():
    hits = [f"{path.relative_to(SRC)}:{number}" for folder in ("ui", "runtime", "agents", "orchestration")
            for path in (SRC / folder).rglob("*.py")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if REMOVED_NL_GATES.search(line) or NL_RELATION_WORDS.search(line)]
    assert hits == []


def test_default_ask_path_does_not_call_a_regex_planner(monkeypatch):
    import linkloom.runtime.task_plan as task_plan
    import linkloom.ui.unified_ask as unified_ask

    for module in (task_plan, unified_ask):
        assert not [name for name in dir(module) if REMOVED_NL_GATES.search(name)]
    assert not (SRC / "runtime" / "team_decision_planner.py").exists()
