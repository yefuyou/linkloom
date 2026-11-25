"""The default product path has no per-role budget slices and no budget-driven Final-only."""
from pathlib import Path
import re

from linkloom.orchestration import OrchestrationLimits
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
    assert set(OrchestrationLimits.__dataclass_fields__) & {"max_run_cost_usd", "max_cost_usd"} == set()
    assert set(TaskProviderLimits.__dataclass_fields__) == {"max_seconds", "request_timeout_seconds"}
    assert limits.provider == TaskProviderLimits(60, 10)


def test_only_the_last_step_forces_final_only_in_the_model_loop():
    text = (SRC / "runtime" / "model_loop.py").read_text(encoding="utf-8")
    assignments = [line.strip() for line in text.splitlines() if re.match(r"\s*final_only = ", line)]
    assert assignments == ['final_only = recovery_decision.action == "FINAL_PARTIAL"',
                           "final_only = True"]
    assert "self.reserve_final_turn and model_steps == self.max_steps" in text
    assert not re.search(r"tool_window|final_only\s*=.*(budget|cost)", text)


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


# Names are split so this file never matches its own scan.
BUDGET_NAMES = re.compile("|".join(["max_run_cost" + "_usd", "RUN_COST" + "_LIMIT", "max_cost" + "_usd",
    "TASK_PROVIDER_BUDGET" + "_EXHAUSTED", "max_provider" + "_requests", "remaining_provider" + "_limits",
    "pass_router" + "_reservation", "requests_" + "reserved", "cost_" + "reserved", "tokens_" + "reserved",
    "estimated_cost_per" + "_request", "run_cost" + "_usd", r"\bBUDGET" + "_EXHAUSTED"]))
# Only legacy readers and comments naming a removed field may keep one. canary_trust.py is a dev-only paid-run
# guard for live canary scripts, outside the production call chain.
LEGACY_READERS = {"runtime/models.py", "tools/semantic_analysis.py", "ui/task_provider.py",
                  "observability/ask_telemetry.py", "evaluation/canary_trust.py"}
# A cumulative usage figure compared with a limit decides nothing in production code.
USAGE_GATE = re.compile(r"\b(?:if|while|elif|assert)\b[^\n#]*\b(?:cost|cost_usd|spent|total_tokens|input_tokens|output_tokens"
                        r"|cumulative\w*|requests|provider_requests)\b\s*(?:>=|>|<=)\s*(?:self\.)?(?:limits?|budget|max_\w+|cap)\b")


def _sources(*folders):
    roots = [SRC / folder for folder in folders] if folders else [SRC]
    return [path for root in roots for path in root.rglob("*.py")]


def test_no_production_file_gates_on_a_removed_budget_name():
    hits = [f"{path.relative_to(SRC).as_posix()}:{number}" for path in _sources()
            if path.relative_to(SRC).as_posix() not in LEGACY_READERS
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if BUDGET_NAMES.search(line)]
    assert hits == []


def test_legacy_readers_only_read_removed_names_and_never_branch_on_them():
    for name in LEGACY_READERS:
        for number, line in enumerate((SRC / name).read_text(encoding="utf-8").splitlines(), 1):
            if BUDGET_NAMES.search(line):
                assert not re.match(r"\s*(?:if|elif|while|raise|assert)\b", line), f"{name}:{number}"


def test_no_cumulative_usage_comparison_gates_execution_in_runtime_paths():
    hits = [f"{path.relative_to(SRC).as_posix()}:{number}" for path in _sources("ui", "orchestration", "runtime", "agents",
                                                                              "semantic_ingestion", "observability")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if USAGE_GATE.search(line)]
    assert hits == []


def test_meeting_extraction_has_no_window_count_limit():
    from linkloom.semantic_ingestion.meeting_tasks_v2 import TaskExtractionConfig
    assert "max_calls" not in TaskExtractionConfig.__dataclass_fields__
    for name in ("meeting_tasks_v2.py", "meeting_tasks_v3.py"):
        text = (SRC / "semantic_ingestion" / name).read_text(encoding="utf-8")
        assert "config.max_calls" not in text and "call limit" not in text


def test_telemetry_package_imports_no_scheduler_or_provider_module():
    forbidden = re.compile(r"^\s*(?:from|import)\s+linkloom\.(?:orchestration|agents\.providers|runtime\.engine"
                           r"|ui\.(?:task_provider|investigation|router_provider|graph_investigation))\b", re.M)
    for path in (SRC / "observability").rglob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name
