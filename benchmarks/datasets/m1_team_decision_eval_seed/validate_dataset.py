"""Validate the synthetic Team Decision & Action seed.

This module intentionally validates only the DATA/SPEC/TEST-DESIGN contract.
It does not import LinkLoom, run an Agent, call a provider, or score a model.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "dataset.jsonl"

WORKSPACES = {
    "model_provider_selection",
    "retrieval_upgrade",
    "deployment_incident",
    "agent_evaluation_rollout",
    "internal_tool_integration",
    "data_retention_migration",
}

SCENARIO_BY_LEVEL = {
    1: "direct_factual_recovery",
    2: "multi_note_synthesis",
    3: "rejected_alternative_reasoning",
    4: "unresolved_action_recovery",
    5: "temporal_stale_conflict",
    6: "ambiguous_insufficient_evidence",
}

EXPECTED_DIFFICULTY_COUNTS = {1: 6, 2: 6, 3: 5, 4: 5, 5: 4, 6: 4}

REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "case_id",
    "workspace_id",
    "difficulty",
    "scenario_type",
    "hard_negative_tags",
    "user_question",
    "source_notes",
    "expected_relevant_notes",
    "distractor_notes",
    "expected_decision",
    "expected_rejected_alternatives",
    "expected_reasoning_points",
    "expected_action_items",
    "expected_unresolved_items",
    "expected_evidence_refs",
    "expected_tool_trajectory",
    "acceptable_alternative_trajectories",
    "trajectory_constraints",
    "required_claims",
    "forbidden_claims",
    "groundedness_requirements",
    "insufficient_evidence_behavior",
    "expected_outcome",
    "metric_mapping",
}

METRIC_REGISTRY = {
    "component": {
        "retrieval_hit_at_k",
        "mrr",
        "tool_selection_correctness",
        "tool_argument_correctness",
    },
    "trajectory": {
        "required_tool_missing",
        "unnecessary_tool_calls",
        "repeated_calls",
        "premature_final",
        "invalid_ordering",
    },
    "outcome": {
        "action_item_accuracy",
        "decision_accuracy",
        "rejected_alternative_accuracy",
        "root_cause_accuracy",
        "unresolved_item_recall",
        "evidence_groundedness",
        "unsupported_claim_rate",
        "task_success",
    },
}

REQUIRED_HARD_NEGATIVE_TAGS = {
    "unsupported_claim",
    "stale_vs_current",
    "mentioned_vs_decided",
    "proposed_vs_assigned",
    "planned_vs_completed",
}

ALLOWED_TRAJECTORY_SYMBOLS = {"search_notes", "read_verified_note", "final"}
EXPECTED_OUTCOMES = {"success", "partial_with_uncertainty", "insufficient_evidence"}
DECISION_STATUSES = {"approved", "partial", "insufficient_evidence", "not_found"}
ACTION_STATUSES = {"completed", "in_progress", "blocked", "unassigned", "pending"}


def _slugify_heading(text: str) -> str:
    """Use the documented GitHub-like ASCII heading slug for this seed."""

    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _is_safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value):
        return False
    if "://" in value or any(part == ".." for part in Path(value).parts):
        return False
    return value.startswith("workspaces/") and value.endswith(".md")


def _assert_string_list(value: object, field: str, errors: list[str]) -> bool:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{field} must be a non-empty list of strings")
        return False
    return True


def _validate_note(path_text: str, errors: list[str]) -> set[str]:
    path = BASE_DIR / Path(path_text)
    if not path.is_file():
        errors.append(f"missing note: {path_text}")
        return set()
    raw = path.read_text(encoding="utf-8")
    if re.search(r"(?:https?|file)://|\b[A-Za-z]:[\\/]|\\\\", raw):
        errors.append(f"network or absolute/private path in note: {path_text}")
    frontmatter = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", raw, re.DOTALL)
    if not frontmatter:
        errors.append(f"note lacks frontmatter: {path_text}")
    else:
        metadata: dict[str, str] = {}
        for line in frontmatter.group("body").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        for key in ("date", "status", "authority"):
            if not metadata.get(key):
                errors.append(f"note {path_text} lacks frontmatter field {key}")
        if metadata.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", metadata["date"]):
            errors.append(f"note {path_text} date is not ISO formatted")
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", raw, re.MULTILINE)
    if len(headings) < 3:
        errors.append(f"note {path_text} needs at least three stable headings")
    slugs = {_slugify_heading(heading) for heading in headings}
    if "" in slugs or len(slugs) != len(headings):
        errors.append(f"note {path_text} has empty or duplicate heading slugs")
    return slugs


def _validate_evidence_ref_list(
    refs: object,
    field: str,
    *,
    source: set[str],
    relevant: set[str],
    distractors: set[str],
    top_level_refs: set[str],
    note_headings: dict[str, set[str]],
    errors: list[str],
    require_top_level_inclusion: bool,
) -> None:
    if not _assert_string_list(refs, field, errors):
        return
    for index, ref in enumerate(refs):
        ref_field = f"{field}[{index}]"
        if ref.count("#") != 1:
            errors.append(f"{ref_field} must contain one #: {ref}")
            continue
        note_path, anchor = ref.split("#", 1)
        if not _is_safe_relative_path(note_path):
            errors.append(f"{ref_field} has an unsafe note path: {note_path}")
        if note_path not in source:
            errors.append(f"{ref_field} note is not in source_notes: {ref}")
        if note_path not in relevant:
            errors.append(f"{ref_field} note is not relevant: {ref}")
        if note_path in distractors:
            errors.append(f"{ref_field} points to a distractor note: {ref}")
        if note_path not in note_headings:
            errors.append(f"{ref_field} evidence note unavailable: {ref}")
        elif not anchor or anchor not in note_headings[note_path]:
            errors.append(f"{ref_field} evidence heading does not exist: {ref}")
        if require_top_level_inclusion and ref not in top_level_refs:
            errors.append(f"{ref_field} is not included in expected_evidence_refs: {ref}")


def _validate_nested_evidence_refs(
    value: object,
    field: str,
    *,
    source: set[str],
    relevant: set[str],
    distractors: set[str],
    top_level_refs: set[str],
    note_headings: dict[str, set[str]],
    errors: list[str],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            if key == "evidence_refs":
                _validate_evidence_ref_list(
                    child,
                    child_field,
                    source=source,
                    relevant=relevant,
                    distractors=distractors,
                    top_level_refs=top_level_refs,
                    note_headings=note_headings,
                    errors=errors,
                    require_top_level_inclusion=True,
                )
            else:
                _validate_nested_evidence_refs(
                    child,
                    child_field,
                    source=source,
                    relevant=relevant,
                    distractors=distractors,
                    top_level_refs=top_level_refs,
                    note_headings=note_headings,
                    errors=errors,
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_nested_evidence_refs(
                child,
                f"{field}[{index}]",
                source=source,
                relevant=relevant,
                distractors=distractors,
                top_level_refs=top_level_refs,
                note_headings=note_headings,
                errors=errors,
            )


def _validate_evidence_refs(case: dict, note_headings: dict[str, set[str]], errors: list[str]) -> None:
    refs = case["expected_evidence_refs"]
    source = set(case["source_notes"]) if isinstance(case["source_notes"], list) else set()
    relevant = set(case["expected_relevant_notes"])
    distractors = set(case["distractor_notes"]) if isinstance(case["distractor_notes"], list) else set()
    top_level_refs = set(refs) if isinstance(refs, list) and all(isinstance(item, str) for item in refs) else set()
    _validate_evidence_ref_list(
        refs,
        f"{case['case_id']}.expected_evidence_refs",
        source=source,
        relevant=relevant,
        distractors=distractors,
        top_level_refs=top_level_refs,
        note_headings=note_headings,
        errors=errors,
        require_top_level_inclusion=False,
    )


def _validate_trajectory(case: dict, errors: list[str]) -> None:
    case_id = case["case_id"]
    expected = case["expected_tool_trajectory"]
    if not isinstance(expected, dict) or set(expected) != {"preferred", "rationale"}:
        errors.append(f"{case_id}.expected_tool_trajectory has the wrong shape")
        return
    if not isinstance(expected["rationale"], str) or not expected["rationale"]:
        errors.append(f"{case_id} trajectory rationale is empty")

    alternatives = case["acceptable_alternative_trajectories"]
    if not isinstance(alternatives, list) or not alternatives:
        errors.append(f"{case_id} needs at least one acceptable alternative trajectory")
        alternatives = []
    constraints = case["trajectory_constraints"]
    expected_constraint_keys = {
        "forbidden_patterns",
        "max_tool_calls",
        "max_calls_by_tool",
        "order_rules",
        "final_requires_evidence",
    }
    if not isinstance(constraints, dict) or set(constraints) != expected_constraint_keys:
        errors.append(f"{case_id}.trajectory_constraints has the wrong shape")
        return
    if "final_without_evidence" not in constraints["forbidden_patterns"]:
        errors.append(f"{case_id} does not forbid final_without_evidence")
    if not isinstance(constraints["max_tool_calls"], int) or constraints["max_tool_calls"] < 1:
        errors.append(f"{case_id} max_tool_calls must be a positive integer")
    if constraints["final_requires_evidence"] is not True:
        errors.append(f"{case_id} final_requires_evidence must be true")
    max_by_tool = constraints["max_calls_by_tool"]
    if not isinstance(max_by_tool, dict):
        errors.append(f"{case_id} max_calls_by_tool must be an object")
        max_by_tool = {}
    for name, limit in max_by_tool.items():
        if name not in {"search_notes", "read_verified_note"} or not isinstance(limit, int) or limit < 1:
            errors.append(f"{case_id} has invalid per-tool call limit: {name}")
    if not isinstance(constraints["forbidden_patterns"], list) or not all(
        isinstance(item, str) and item for item in constraints["forbidden_patterns"]
    ):
        errors.append(f"{case_id} forbidden_patterns must be a string list")
    if not isinstance(constraints["order_rules"], list) or not all(
        isinstance(item, str) and item for item in constraints["order_rules"]
    ):
        errors.append(f"{case_id} order_rules must be a string list")

    trajectories = [("preferred", expected.get("preferred"))]
    trajectories.extend((f"alternative[{index}]", item.get("trajectory") if isinstance(item, dict) else None) for index, item in enumerate(alternatives))
    seen_trajectories: list[tuple[str, list]] = []
    for label, trajectory in trajectories:
        if isinstance(trajectory, list):
            duplicate_of = next(
                (
                    previous_label
                    for previous_label, previous_trajectory in seen_trajectories
                    if trajectory == previous_trajectory
                ),
                None,
            )
            if duplicate_of is not None:
                errors.append(f"{case_id} {label} duplicates {duplicate_of} trajectory")
            else:
                seen_trajectories.append((label, trajectory))
        if not isinstance(trajectory, list) or not trajectory or not all(item in ALLOWED_TRAJECTORY_SYMBOLS for item in trajectory):
            errors.append(f"{case_id} {label} is not an allowed non-empty trajectory")
            continue
        if trajectory[-1] != "final":
            errors.append(f"{case_id} {label} must end in final")
        if trajectory.count("final") != 1:
            errors.append(f"{case_id} {label} must contain exactly one final")
        tool_calls = [item for item in trajectory if item != "final"]
        if len(tool_calls) > constraints["max_tool_calls"]:
            errors.append(f"{case_id} {label} exceeds max_tool_calls")
        for tool_name, limit in max_by_tool.items():
            if tool_calls.count(tool_name) > limit:
                errors.append(f"{case_id} {label} exceeds {tool_name} limit")
        final_index = trajectory.index("final")
        if constraints["final_requires_evidence"] and not any(
            item in {"search_notes", "read_verified_note"} for item in trajectory[:final_index]
        ):
            errors.append(f"{case_id} {label} is final_without_evidence")
        if "read_after_search" in constraints["order_rules"] and "read_verified_note" in trajectory:
            if "search_notes" not in trajectory[: trajectory.index("read_verified_note")]:
                errors.append(f"{case_id} {label} reads before search")

    for index, item in enumerate(alternatives):
        if not isinstance(item, dict) or set(item) != {"trajectory", "condition"}:
            errors.append(f"{case_id} alternative[{index}] has the wrong shape")
        elif not isinstance(item["condition"], str) or not item["condition"]:
            errors.append(f"{case_id} alternative[{index}] condition is empty")


def _validate_structured_fields(case: dict, note_headings: dict[str, set[str]], errors: list[str]) -> None:
    case_id = case["case_id"]
    decision = case["expected_decision"]
    if not isinstance(decision, dict) or set(decision) != {"value", "status", "evidence_refs"}:
        errors.append(f"{case_id}.expected_decision has the wrong shape")
    elif decision["status"] not in DECISION_STATUSES or not isinstance(decision["value"], (str, type(None))):
        errors.append(f"{case_id}.expected_decision status/value invalid")
    elif not isinstance(decision["evidence_refs"], list) or not decision["evidence_refs"]:
        errors.append(f"{case_id}.expected_decision evidence_refs must be non-empty")

    for field in ("expected_rejected_alternatives", "expected_reasoning_points", "expected_action_items", "expected_unresolved_items"):
        if not isinstance(case[field], list):
            errors.append(f"{case_id}.{field} must be a list")
    for field in ("expected_rejected_alternatives", "expected_reasoning_points"):
        for index, item in enumerate(case[field]):
            if not isinstance(item, dict) or "evidence_refs" not in item or not isinstance(item["evidence_refs"], list) or not item["evidence_refs"]:
                errors.append(f"{case_id}.{field}[{index}] needs evidence_refs")
    for field in ("expected_action_items", "expected_unresolved_items"):
        for index, item in enumerate(case[field]):
            required = {"id", "description", "owner", "deadline", "status", "evidence_refs"}
            if not isinstance(item, dict) or set(item) != required:
                errors.append(f"{case_id}.{field}[{index}] has the wrong shape")
            elif item["status"] not in ACTION_STATUSES or not isinstance(item["evidence_refs"], list) or not item["evidence_refs"]:
                errors.append(f"{case_id}.{field}[{index}] has invalid status/evidence")

    source = set(case["source_notes"]) if isinstance(case["source_notes"], list) else set()
    relevant = set(case["expected_relevant_notes"]) if isinstance(case["expected_relevant_notes"], list) else set()
    distractors = set(case["distractor_notes"]) if isinstance(case["distractor_notes"], list) else set()
    top_level_refs = (
        set(case["expected_evidence_refs"])
        if isinstance(case["expected_evidence_refs"], list)
        and all(isinstance(item, str) for item in case["expected_evidence_refs"])
        else set()
    )
    for field in (
        "expected_decision",
        "expected_rejected_alternatives",
        "expected_reasoning_points",
        "expected_action_items",
        "expected_unresolved_items",
    ):
        _validate_nested_evidence_refs(
            case[field],
            f"{case_id}.{field}",
            source=source,
            relevant=relevant,
            distractors=distractors,
            top_level_refs=top_level_refs,
            note_headings=note_headings,
            errors=errors,
        )

    for field in ("required_claims", "forbidden_claims"):
        _assert_string_list(case[field], f"{case_id}.{field}", errors)
    if isinstance(case["required_claims"], list) and len(set(case["required_claims"])) != len(case["required_claims"]):
        errors.append(f"{case_id} required_claims repeat")
    if isinstance(case["forbidden_claims"], list) and len(set(case["forbidden_claims"])) != len(case["forbidden_claims"]):
        errors.append(f"{case_id} forbidden_claims repeat")
    if set(case.get("required_claims", [])) & set(case.get("forbidden_claims", [])):
        errors.append(f"{case_id} required and forbidden claims overlap")

    grounded = case["groundedness_requirements"]
    grounded_keys = {"evidence_required_for_each_claim", "current_over_memory_precedence", "must_separate_fact_from_suggestion", "allowed_evidence_scopes"}
    if not isinstance(grounded, dict) or set(grounded) != grounded_keys:
        errors.append(f"{case_id}.groundedness_requirements has the wrong shape")
    elif grounded["evidence_required_for_each_claim"] is not True or grounded["current_over_memory_precedence"] is not True or grounded["must_separate_fact_from_suggestion"] is not True or not isinstance(grounded["allowed_evidence_scopes"], list) or not grounded["allowed_evidence_scopes"]:
        errors.append(f"{case_id}.groundedness_requirements violates required grounding flags")

    insufficient = case["insufficient_evidence_behavior"]
    insufficient_keys = {"outcome", "must_state_uncertainty", "must_not_invent", "required_distinction"}
    if not isinstance(insufficient, dict) or set(insufficient) != insufficient_keys:
        errors.append(f"{case_id}.insufficient_evidence_behavior has the wrong shape")
    elif insufficient["outcome"] not in EXPECTED_OUTCOMES or insufficient["must_state_uncertainty"] is not True or insufficient["must_not_invent"] is not True or not isinstance(insufficient["required_distinction"], str) or not insufficient["required_distinction"]:
        errors.append(f"{case_id}.insufficient_evidence_behavior is invalid")

    if case["expected_outcome"] not in EXPECTED_OUTCOMES:
        errors.append(f"{case_id} has invalid expected_outcome")

    metric_mapping = case["metric_mapping"]
    if not isinstance(metric_mapping, dict) or set(metric_mapping) != set(METRIC_REGISTRY):
        errors.append(f"{case_id}.metric_mapping must contain component/trajectory/outcome")
    else:
        for layer, registry in METRIC_REGISTRY.items():
            values = metric_mapping[layer]
            if not isinstance(values, list) or not values or not all(value in registry for value in values):
                errors.append(f"{case_id}.metric_mapping.{layer} contains an unknown or empty metric")


def main() -> int:
    errors: list[str] = []
    if not DATASET_PATH.is_file():
        print(f"ERROR: missing {DATASET_PATH}")
        return 1

    note_paths = sorted(BASE_DIR.glob("workspaces/*/*.md"))
    expected_note_paths = {
        f"workspaces/{workspace}/{path.name}"
        for workspace in WORKSPACES
        for path in sorted((BASE_DIR / "workspaces" / workspace).glob("*.md"))
    }
    if len(note_paths) != 36:
        errors.append(f"expected 36 workspace notes, found {len(note_paths)}")
    for workspace in WORKSPACES:
        count = len(list((BASE_DIR / "workspaces" / workspace).glob("*.md")))
        if count != 6:
            errors.append(f"workspace {workspace} must contain 6 notes, found {count}")

    note_headings: dict[str, set[str]] = {}
    for path in note_paths:
        relative = path.relative_to(BASE_DIR).as_posix()
        note_headings[relative] = _validate_note(relative, errors)

    cases: list[dict] = []
    with DATASET_PATH.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                errors.append(f"blank JSONL line {line_number}")
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid JSON on line {line_number}: {exc.msg}")
                continue
            if not isinstance(record, dict):
                errors.append(f"line {line_number} is not a JSON object")
                continue
            cases.append(record)

    if len(cases) != 30:
        errors.append(f"expected 30 cases, found {len(cases)}")
    case_ids = [case.get("case_id") for case in cases]
    if len(set(case_ids)) != len(case_ids):
        errors.append("case_id values must be unique")
    workspace_counts: Counter[str] = Counter()
    difficulty_counts: Counter[int] = Counter()
    hard_tags: set[str] = set()
    insufficient_count = 0
    stale_count = 0
    distractor_workspaces: set[str] = set()

    for case in cases:
        case_id = case.get("case_id", "<missing-case-id>")
        if set(case) != REQUIRED_TOP_LEVEL_FIELDS:
            missing = REQUIRED_TOP_LEVEL_FIELDS - set(case)
            extra = set(case) - REQUIRED_TOP_LEVEL_FIELDS
            if missing:
                errors.append(f"{case_id} missing top-level fields: {sorted(missing)}")
            if extra:
                errors.append(f"{case_id} has unexpected top-level fields: {sorted(extra)}")
            continue
        if case["schema_version"] != "team-decision-eval-case/v1":
            errors.append(f"{case_id} has wrong schema_version")
        workspace = case["workspace_id"]
        if workspace not in WORKSPACES:
            errors.append(f"{case_id} has unknown workspace {workspace}")
        else:
            workspace_counts[workspace] += 1
        difficulty = case["difficulty"]
        if difficulty not in SCENARIO_BY_LEVEL:
            errors.append(f"{case_id} has invalid difficulty")
        else:
            difficulty_counts[difficulty] += 1
            if case["scenario_type"] != SCENARIO_BY_LEVEL[difficulty]:
                errors.append(f"{case_id} scenario_type does not match difficulty")
            if difficulty == 5:
                stale_count += 1
            if difficulty == 6:
                insufficient_count += 1
        tags = case["hard_negative_tags"]
        if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) and tag for tag in tags):
            errors.append(f"{case_id} hard_negative_tags must be non-empty strings")
        else:
            if len(set(tags)) != len(tags):
                errors.append(f"{case_id} hard_negative_tags repeat")
            hard_tags.update(tags)

        for field in ("source_notes", "expected_relevant_notes", "distractor_notes"):
            values = case[field]
            if not _assert_string_list(values, f"{case_id}.{field}", errors):
                continue
            if len(set(values)) != len(values):
                errors.append(f"{case_id}.{field} repeats a path")
            for path_text in values:
                if not _is_safe_relative_path(path_text):
                    errors.append(f"{case_id} unsafe note path: {path_text}")
                if path_text not in expected_note_paths:
                    errors.append(f"{case_id} note path does not exist in seed: {path_text}")
                if not path_text.startswith(f"workspaces/{workspace}/"):
                    errors.append(f"{case_id} note path crosses workspace boundary: {path_text}")
            if field == "distractor_notes" and values:
                distractor_workspaces.add(workspace)
        source = set(case["source_notes"]) if isinstance(case["source_notes"], list) else set()
        relevant = set(case["expected_relevant_notes"]) if isinstance(case["expected_relevant_notes"], list) else set()
        distractors = set(case["distractor_notes"]) if isinstance(case["distractor_notes"], list) else set()
        if not relevant <= source:
            errors.append(f"{case_id} expected_relevant_notes is not a source_notes subset")
        if not distractors <= source:
            errors.append(f"{case_id} distractor_notes is not a source_notes subset")
        if relevant & distractors:
            errors.append(f"{case_id} relevant and distractor notes overlap")
        _validate_evidence_refs(case, note_headings, errors)
        _validate_trajectory(case, errors)
        _validate_structured_fields(case, note_headings, errors)

    if dict(difficulty_counts) != EXPECTED_DIFFICULTY_COUNTS:
        errors.append(f"difficulty distribution mismatch: {dict(difficulty_counts)}")
    if any(workspace_counts[workspace] != 5 for workspace in WORKSPACES):
        errors.append(f"each workspace needs 5 cases: {dict(workspace_counts)}")
    if not REQUIRED_HARD_NEGATIVE_TAGS <= hard_tags:
        errors.append(f"missing required hard-negative coverage: {sorted(REQUIRED_HARD_NEGATIVE_TAGS - hard_tags)}")
    if stale_count < 4:
        errors.append(f"need at least 4 stale/conflict cases, found {stale_count}")
    if insufficient_count < 4:
        errors.append(f"need at least 4 insufficient/ambiguous cases, found {insufficient_count}")
    if distractor_workspaces != WORKSPACES:
        errors.append(f"every workspace needs a distractor case: {sorted(distractor_workspaces)}")

    serialized = DATASET_PATH.read_text(encoding="utf-8")
    if re.search(r"(?:https?|file)://|\b[A-Za-z]:[\\/]|\\\\|api[_-]?key|secret", serialized, re.IGNORECASE):
        errors.append("dataset contains a network reference, private path, or secret-like token")

    if errors:
        print(f"FAIL: {len(errors)} validation error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: 30 cases, 6 workspaces, 36 notes")
    print(f"PASS: difficulty={dict(sorted(difficulty_counts.items()))}")
    print(f"PASS: hard_negative_tags={sorted(hard_tags)}")
    print(f"PASS: stale_or_conflict={stale_count}; insufficient_or_ambiguous={insufficient_count}")
    print("PASS: paths, evidence anchors, trajectory constraints, claims, and metric registry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
