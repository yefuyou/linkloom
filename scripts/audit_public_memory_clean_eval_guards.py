"""Build an offline inventory of CleanEvalBlocked production throw sites."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from scripts.run_public_memory_clean_eval import (
    _clean_eval_reason_category,
    _guard_criticality_level,
    _stable_clean_eval_reason_code,
)


REPO = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO / "scripts" / "run_public_memory_clean_eval.py"


def _parents(root: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(root)
        for child in ast.iter_child_nodes(parent)
    }


def _nearest(node: ast.AST, parents: dict[ast.AST, ast.AST], kinds: tuple[type, ...]) -> ast.AST | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, kinds):
            return current
    return None


def _function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    function = _nearest(node, parents, (ast.FunctionDef, ast.AsyncFunctionDef))
    return function.name if function is not None else "<module>"


def _accepted_response_precedes(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    attempt = _nearest(node, parents, (ast.Try,))
    if attempt is None:
        return False
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "generate_content"
        and child.lineno < node.lineno
        for child in ast.walk(attempt)
    )


def _phase(node: ast.AST, function: str, response_may_be_accepted: bool) -> str:
    if function == "_response_text":
        return "RESPONSE_VALIDATION"
    if function.startswith("_score") or "gold" in function.casefold():
        return "SCORING_PRECONDITION"
    if "journal" in function.casefold() or "persist" in function.casefold() or "record_case" in function.casefold():
        return "ARTIFACT_PERSISTENCE"
    if response_may_be_accepted:
        return "POST_RESPONSE"
    if function.startswith("_verify") or "preflight" in function.casefold():
        return "PREFLIGHT"
    if function == "_run_live":
        return "RUN_LIFECYCLE"
    return "OFFLINE_OR_FINALIZATION"


def build_guard_inventory(source_path: Path = RUNNER_PATH) -> dict[str, Any]:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parents(tree)
    sites: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "CleanEvalBlocked"
        ):
            continue
        args = node.exc.args
        reason_expression = ast.unparse(args[0]) if args else "<MISSING_REASON>"
        reason_code = _stable_clean_eval_reason_code(reason_expression)
        category = _clean_eval_reason_category(reason_expression)
        function = _function_name(node, parents)
        response_may_be_accepted = (
            _accepted_response_precedes(node, parents)
            or function == "_response_text"
            or function.startswith("_score")
        )
        condition = _nearest(node, parents, (ast.If,))
        handler = _nearest(node, parents, (ast.ExceptHandler,))
        guard_condition = (
            ast.unparse(condition.test)
            if isinstance(condition, ast.If)
            else f"exception handler {ast.unparse(handler.type) if isinstance(handler, ast.ExceptHandler) and handler.type else 'BaseException'}"
            if isinstance(handler, ast.ExceptHandler)
            else "unconditional or exception-handler guard"
        )
        sites.append(
            {
                "file": source_path.relative_to(REPO).as_posix(),
                "line": node.lineno,
                "function": function,
                "has_reason_argument": bool(args),
                "phase": _phase(node, function, response_may_be_accepted),
                "guard_condition": guard_condition,
                "reason_expression": reason_expression,
                "reason_code_pattern": reason_code,
                "failure_category": category,
                "criticality_level": _guard_criticality_level(reason_code),
                "default_policy_action": {
                    "EVALUATION_CRITICAL": "RUN_HARD_STOP",
                    "SCOREABILITY_CRITICAL": "CASE_NOT_EVALUATED",
                    "COST_TELEMETRY_DEGRADED": "CONTINUE_WITH_DEGRADED_TELEMETRY",
                    "OBSERVABILITY_FORMAT_ONLY": "WARNING_ONLY",
                }[_guard_criticality_level(reason_code)],
                "provider_response_may_already_be_accepted": response_may_be_accepted,
                "retry_meaningful": False,
                "safe_message": "A local evaluation guard blocked execution.",
            }
        )
    sites.sort(key=lambda row: (row["file"], row["line"]))
    missing = [
        site for site in sites
        if not site["has_reason_argument"]
        or not site["reason_code_pattern"].startswith("CLEAN_EVAL_BLOCK_")
        or site["reason_code_pattern"].endswith("_OTHER_UNCLASSIFIED")
    ]
    unclassified_criticality = [
        site for site in sites
        if site["criticality_level"] not in {
            "EVALUATION_CRITICAL",
            "SCOREABILITY_CRITICAL",
            "COST_TELEMETRY_DEGRADED",
            "OBSERVABILITY_FORMAT_ONLY",
        }
    ]
    return {
        "schema_version": "linkloom-clean-eval-guard-inventory/v1",
        "status": "PASS" if sites and not missing and not unclassified_criticality else "FAIL",
        "source_file": source_path.relative_to(REPO).as_posix(),
        "production_reachable_throw_sites": len(sites),
        "uninstrumented_throw_sites": len(missing),
        "unclassified_criticality_sites": len(unclassified_criticality),
        "criticality_counts": {
            level: sum(site["criticality_level"] == level for site in sites)
            for level in (
                "EVALUATION_CRITICAL",
                "SCOREABILITY_CRITICAL",
                "COST_TELEMETRY_DEGRADED",
                "OBSERVABILITY_FORMAT_ONLY",
            )
        },
        "provider_calls": 0,
        "gold_values_read": False,
        "sites": sites,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(build_guard_inventory(), ensure_ascii=False, indent=2))
