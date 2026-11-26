"""Small, optional subgoal contract derived from durable retrieval facts.

Plans describe questions and dependencies, never answers. The model writes the
plan; the Host validates its structure only (IDs, bounds, dependency order,
relation-path types, placeholders), never its wording. A subgoal counts as
observed only after its search hit has been read back as the same verified
source passage. All state is reconstructed from the existing Tool Ledger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from linkloom.agents.model_adapter import is_verified_evidence_result
from linkloom.agents.claim_grounding import TYPED_CLAIM_RELATIONS, evidence_identity_valid
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.tools.contracts import ToolResult


MAX_TASK_PLAN_SUBGOALS = 5


@dataclass(frozen=True, slots=True)
class Subgoal:
    subgoal_id: str
    question: str
    depends_on: tuple[str, ...] = ()
    alternate_question: str | None = None
    relation_path: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"subgoal_id": self.subgoal_id, "question": self.question,
                  "depends_on": list(self.depends_on)}
        if self.alternate_question is not None:
            result["alternate_question"] = self.alternate_question
        if self.relation_path is not None:
            result["relation_path"] = list(self.relation_path)
        return result


@dataclass(frozen=True, slots=True)
class TaskPlan:
    goal: str
    subgoals: tuple[Subgoal, ...]
    schema_version: str = "task-plan/v1"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskPlan":
        if (not isinstance(value, dict) or not {"goal", "subgoals"} <= set(value)
                or set(value) - {"goal", "subgoals", "schema_version"}):
            raise ValidationError("TaskPlan requires only goal and subgoals.")
        version = value.get("schema_version", "task-plan/v1")
        if version not in {"task-plan/v1", "task-plan/v2"}:
            raise ValidationError("TaskPlan schema version is unsupported.")
        goal, rows = value["goal"], value["subgoals"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 2048:
            raise ValidationError("TaskPlan.goal must be bounded non-empty text.")
        if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_TASK_PLAN_SUBGOALS:
            raise ValidationError("TaskPlan needs two to five subgoals.")
        parsed: list[Subgoal] = []
        prior: set[str] = set()
        for row in rows:
            if (not isinstance(row, dict)
                    or not {"subgoal_id", "question", "depends_on"} <= set(row)
                    or set(row) - {"subgoal_id", "question", "depends_on", "alternate_question", "relation_path"}):
                raise ValidationError("TaskPlan subgoal has invalid fields.")
            if version == "task-plan/v2" and "relation_path" not in row:
                raise ValidationError("TaskPlan/v2 requires a relation_path for every subgoal.")
            if version == "task-plan/v1" and "relation_path" in row:
                raise ValidationError("TaskPlan/v1 does not define relation_path.")
            subgoal_id, question, dependencies = (
                row["subgoal_id"], row["question"], row["depends_on"]
            )
            if (not isinstance(subgoal_id, str) or not subgoal_id.isidentifier()
                    or len(subgoal_id) > 64 or subgoal_id in prior):
                raise ValidationError("TaskPlan subgoal ID is invalid or duplicated.")
            if not isinstance(question, str) or not question.strip() or len(question) > 512:
                raise ValidationError("TaskPlan subgoal question must be bounded text.")
            alternate = row.get("alternate_question")
            if alternate is not None and (
                not isinstance(alternate, str) or not alternate.strip()
                or len(alternate) > 512 or alternate.strip() == question.strip()
            ):
                raise ValidationError("TaskPlan alternate question must be distinct bounded text.")
            if (not isinstance(dependencies, list) or len(dependencies) > 2
                    or any(not isinstance(item, str) or item not in prior
                           for item in dependencies)
                    or len(set(dependencies)) != len(dependencies)):
                raise ValidationError("TaskPlan dependencies must precede their subgoal.")
            if version == "task-plan/v2" and len(dependencies) > 1:
                raise ValidationError("TaskPlan/v2 relation paths consume one verified parent.")
            path = row.get("relation_path")
            if path is not None:
                if (not isinstance(path, list) or len(path) > 2
                        or any(not isinstance(item, str) or item not in TYPED_CLAIM_RELATIONS
                               for item in path)
                        or (len(path) == 2 and path != ["alias_of_project", "owner_of_project"])
                        or (dependencies and not path)):
                    raise ValidationError("TaskPlan relation_path is not a supported directed chain.")
                for parent_id in dependencies:
                    parent_path = next(item.relation_path for item in parsed if item.subgoal_id == parent_id)
                    if parent_path and path and (
                        TYPED_CLAIM_RELATIONS[parent_path[-1]]["object_type"]
                        != TYPED_CLAIM_RELATIONS[path[0]]["subject_type"]
                    ):
                        raise ValidationError("TaskPlan dependency relation types do not connect.")
            parsed.append(Subgoal(subgoal_id, question.strip(), tuple(dependencies),
                                  alternate.strip() if alternate is not None else None,
                                  tuple(path) if path is not None else None))
            prior.add(subgoal_id)
        return cls(goal.strip(), tuple(parsed), version)

    def to_dict(self) -> dict[str, Any]:
        result = {"goal": self.goal, "subgoals": [item.to_dict() for item in self.subgoals]}
        if self.schema_version != "task-plan/v1":
            result["schema_version"] = self.schema_version
        return result

    def get(self, subgoal_id: str) -> Subgoal:
        for item in self.subgoals:
            if item.subgoal_id == subgoal_id:
                return item
        raise ValidationError("Unknown TaskPlan subgoal ID.")


def _canonicalize_model_plan_ids(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize only ordinal IDs and dependency encodings with one meaning."""
    if not isinstance(value, dict) or not isinstance(value.get("subgoals"), list):
        return value
    rows = value["subgoals"]
    mapping: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("subgoal_id")
        if isinstance(identifier, bool):
            raise ValidationError("Boolean TaskPlan subgoal IDs are invalid.")
        numeric_identifier = (
            identifier if isinstance(identifier, int)
            else int(identifier)
            if isinstance(identifier, str) and re.fullmatch(r"[0-9]+", identifier)
            else None
        )
        if numeric_identifier is not None:
            if numeric_identifier <= 0 or numeric_identifier > 999 or numeric_identifier in mapping:
                raise ValidationError("Numeric TaskPlan subgoal ID is invalid or duplicated.")
            mapping[numeric_identifier] = f"step_{numeric_identifier}"
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            normalized.append(row)
            continue
        updated = dict(row)
        identifier = updated.get("subgoal_id")
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            updated["subgoal_id"] = mapping[identifier]
        elif isinstance(identifier, str) and re.fullmatch(r"[0-9]+", identifier):
            updated["subgoal_id"] = mapping[int(identifier)]
        dependencies = updated.get("depends_on")
        if "depends_on" not in updated and index == 0:
            # The first ordered subgoal has no valid earlier dependency.
            updated["depends_on"] = []
        elif isinstance(dependencies, str) and dependencies:
            # A single dependency ID has one unambiguous array representation.
            dependencies = [dependencies]
        if isinstance(dependencies, list):
            updated["depends_on"] = [
                mapping.get(item if isinstance(item, int) else int(item), item)
                if (isinstance(item, int) and not isinstance(item, bool))
                or (isinstance(item, str) and re.fullmatch(r"[0-9]+", item))
                else item
                for item in dependencies
            ]
        normalized.append(updated)
    return {**value, "subgoals": normalized}


def validate_model_proposed_plan(
    value: dict[str, Any], *, user_query: str, max_steps: int,
) -> TaskPlan:
    """Accept a model plan by structure only.

    The model may propose any natural-language subgoals. It cannot supply a
    verified entity (only the placeholder below), change the user goal or
    exceed the step bound. Tool permissions are enforced by the Tool ACL, not
    by reading the question text.
    """
    plan = TaskPlan.from_dict(_canonicalize_model_plan_ids(value))
    if plan.goal != user_query.strip():
        raise ValidationError("Model plan goal differs from the user query.")
    if max_steps < 2 * len(plan.subgoals) + 1:
        raise ValidationError("Model plan exceeds the available search/read/final steps.")
    for subgoal in plan.subgoals:
        for question in (subgoal.question, subgoal.alternate_question):
            if question is None:
                continue
            placeholders = re.findall(r"\{[^{}]*\}", question)
            if any(value != "{verified_entity}" for value in placeholders):
                raise ValidationError("Model plan contains an unapproved placeholder.")
            if subgoal.depends_on and "{verified_entity}" not in question:
                raise ValidationError("Dependent model questions must use a verified entity placeholder.")
            if not subgoal.depends_on and "{verified_entity}" in question:
                raise ValidationError("A root model question cannot claim a verified entity.")
    return plan


def _passage_identity(
    value: dict[str, Any], expected_workspace_id: str,
) -> tuple[Any, ...] | None:
    """Validate an immutable passage against the active workspace before pairing."""
    if not evidence_identity_valid(value, expected_workspace_id):
        return None
    return (expected_workspace_id, value["relative_path"], value["evidence_id"], value["content_sha256"],
            value["line_start"], value["line_end"], value["quote_sha256"])


def _verified_values(record: ToolExecutionRecord) -> list[dict[str, Any]]:
    if record.status != "completed" or record.result is None:
        return []
    try:
        result = ToolResult.from_dict(record.result)
    except ValidationError:
        return []
    if (result.call_id, result.tool_id, result.status) != (
        record.call_id, record.tool_id, "ok"
    ):
        return []
    if not is_verified_evidence_result(result):
        return []
    values = result.value if isinstance(result.value, list) else [result.value]
    return [value for value in values if isinstance(value, dict)]


@dataclass(frozen=True, slots=True)
class PlanAssessment:
    evidence_by_subgoal: dict[str, tuple[dict[str, Any], ...]]
    missing_subgoals: tuple[str, ...]
    status_by_subgoal: dict[str, str]

    @property
    def complete(self) -> bool:
        return not self.missing_subgoals

    def evidence_refs(self) -> set[str]:
        return {
            value["evidence_id"]
            for rows in self.evidence_by_subgoal.values()
            for value in rows
        }

    def cites_each_subgoal(self, cited_refs: set[str]) -> bool:
        """Require one cited verified read per subgoal, not every exploratory read."""
        return all(
            any(value["evidence_id"] in cited_refs for value in rows)
            for rows in self.evidence_by_subgoal.values()
        )


def _entity_in_reads(entity: object, reads: Iterable[dict[str, Any]]) -> bool:
    """A dependency entity is a literal string copied from a verified parent read."""
    if not isinstance(entity, str) or not entity.strip() or len(entity) > 128:
        return False
    minimum_length = 2 if any("\u3400" <= char <= "\u9fff" for char in entity) else 3
    if len(entity.strip()) < minimum_length:
        return False
    normalized = " ".join(entity.split()).casefold()
    pattern = re.compile(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)")
    return any(pattern.search(" ".join(str(value["quote"]).split()).casefold()) for value in reads)


def assess_task_plan(
    plan: TaskPlan,
    records: Iterable[ToolExecutionRecord],
    *,
    run_id: str,
    task_id: str,
    agent_id: str,
    expected_workspace_id: str,
) -> PlanAssessment:
    """Pair each verified read with a preceding tagged search in this task.

    This is provenance bookkeeping, not answer checking: what a read means is
    stated by the model's typed claims and checked at Final.
    """
    if not isinstance(expected_workspace_id, str) or not expected_workspace_id:
        raise ValidationError("TaskPlan assessment requires a workspace ID.")
    observed: dict[str, list[dict[str, Any]]] = {
        item.subgoal_id: [] for item in plan.subgoals
    }
    attempts = {item.subgoal_id: 0 for item in plan.subgoals}
    found = {item.subgoal_id: False for item in plan.subgoals}
    pending_hits: dict[tuple[Any, ...], list[str]] = {}
    verified_reads: dict[tuple[Any, ...], dict[str, Any]] = {}
    scoped = sorted(
        (record for record in records
         if (record.run_id, record.task_id, record.agent_id)
         == (run_id, task_id, agent_id)),
        key=lambda record: (record.sequence, record.call_id),
    )
    for record in scoped:
        values = _verified_values(record)
        if record.tool_id == "search_notes":
            source_context = record.arguments.get("source_context", {})
            subgoal_id = source_context.get("subgoal_id") if isinstance(source_context, dict) else None
            if subgoal_id not in observed:
                continue
            if record.status in {"completed", "failed"}:
                attempts[subgoal_id] += 1
            subgoal = plan.get(subgoal_id)
            dependencies = source_context.get("dependency_entities", {})
            bound = isinstance(dependencies, dict) and set(dependencies) == set(subgoal.depends_on) and all(
                _entity_in_reads(dependencies[parent], observed[parent]) for parent in subgoal.depends_on)
            for value in values:
                identity = _passage_identity(value, expected_workspace_id)
                if identity is not None:
                    found[subgoal_id] = True
                    pending_hits.setdefault(identity, []).append(subgoal_id)
                    prior_read = verified_reads.get(identity)
                    if prior_read is not None and bound and all(
                        _passage_identity(old, expected_workspace_id) != identity
                        for old in observed[subgoal_id]
                    ):
                        observed[subgoal_id].append(prior_read)
        elif record.tool_id == "read_verified_note":
            for value in values:
                identity = _passage_identity(value, expected_workspace_id)
                if identity is None:
                    continue
                slots = pending_hits.pop(identity, None)
                if not slots:
                    continue
                verified_reads[identity] = value
                for subgoal_id in dict.fromkeys(slots):
                    if all(_passage_identity(old, expected_workspace_id) != identity
                           for old in observed[subgoal_id]):
                        observed[subgoal_id].append(value)
    statuses = {}
    for subgoal in plan.subgoals:
        key = subgoal.subgoal_id
        if observed[key]:
            statuses[key] = "completed"
        elif any(not observed[parent] for parent in subgoal.depends_on):
            statuses[key] = "pending"
        elif not found[key] and attempts[key] >= (
            2 if subgoal.alternate_question is not None or len(subgoal.depends_on) > 1 else 1
        ):
            statuses[key] = "blocked"
        else:
            statuses[key] = "ready"
    return PlanAssessment(
        evidence_by_subgoal={key: tuple(value) for key, value in observed.items()},
        missing_subgoals=tuple(key for key, value in observed.items() if not value),
        status_by_subgoal=statuses,
    )


def refusal_missing_subgoals(plan: TaskPlan, assessment: PlanAssessment) -> tuple[str, ...]:
    """Subgoals without a verified read, plus every subgoal that depends on one."""
    missing = set(assessment.missing_subgoals)
    for child in plan.subgoals:
        if any(parent in missing for parent in child.depends_on):
            missing.add(child.subgoal_id)
    return tuple(item.subgoal_id for item in plan.subgoals if item.subgoal_id in missing)


def pending_alternate_subgoals(
    plan: TaskPlan, assessment: PlanAssessment, records: Iterable[ToolExecutionRecord],
    *, run_id: str, task_id: str, agent_id: str,
) -> tuple[str, ...]:
    """Return unread subgoals with one completed primary search and an unused planned alternate."""
    completed = {
        (context["subgoal_id"], context.get("query_variant", "primary"))
        for record in records
        if (record.run_id, record.task_id, record.agent_id) == (run_id, task_id, agent_id)
        and record.tool_id == "search_notes" and record.status == "completed"
        and isinstance((context := record.arguments.get("source_context")), dict)
        and isinstance(context.get("subgoal_id"), str)
    }
    return tuple(
        subgoal.subgoal_id for subgoal in plan.subgoals
        if subgoal.alternate_question is not None
        and (subgoal.subgoal_id, "primary") in completed
        and (subgoal.subgoal_id, "alternate") not in completed
        and not assessment.evidence_by_subgoal[subgoal.subgoal_id]
    )


def verified_dependency_entities(
    plan: TaskPlan,
    assessment: PlanAssessment,
    subgoal_id: str,
    proposed: dict[str, str],
) -> tuple[str, ...]:
    """Accept only entities literally present in each verified parent passage."""
    subgoal = plan.get(subgoal_id)
    if set(proposed) != set(subgoal.depends_on):
        raise ValidationError("Each dependency requires one observed entity.")
    entities: list[str] = []
    for parent in subgoal.depends_on:
        entity = proposed[parent]
        if not _entity_in_reads(entity, assessment.evidence_by_subgoal[parent]):
            raise ValidationError("Dependency entity was not found in verified parent evidence.")
        entities.append(" ".join(entity.split()))
    return tuple(entities)


__all__ = ["TaskPlan", "Subgoal", "PlanAssessment", "assess_task_plan",
           "refusal_missing_subgoals", "pending_alternate_subgoals",
           "verified_dependency_entities", "validate_model_proposed_plan"]
