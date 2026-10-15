"""Small, optional subgoal contract derived from durable retrieval facts.

Plans describe questions and dependencies, never answers. A subgoal counts as
observed only after its search hit has been read back as the same verified
source passage. All state is reconstructed from the existing Tool Ledger.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from linkloom.agents.model_adapter import is_verified_evidence_result
from linkloom.agents.claim_grounding import (
    verified_dependency_values, verified_subgoal_values,
    verified_subgoal_conflict_refs, verified_dependent_leader_values,
    verified_relation_path_values, verified_owner_source_binding,
    TYPED_CLAIM_RELATIONS,
    assess_claim_text, ClaimSupportStatus, _question_relation_entity,
    verified_relation_conflict_refs,
)
from linkloom.evidence_identity import passage_evidence_id
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.tools.contracts import ToolResult


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
        if not isinstance(rows, list) or not 2 <= len(rows) <= 5:
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
    """Accept only bounded read-only questions rooted in the trusted user goal.

    The model may suggest a dependency, but it cannot supply a verified entity,
    grant a new tool, or turn source text into a new user instruction.
    """
    plan = TaskPlan.from_dict(_canonicalize_model_plan_ids(value))
    if plan.goal != user_query.strip():
        raise ValidationError("Model plan goal differs from the user query.")
    if max_steps < 2 * len(plan.subgoals) + 1:
        raise ValidationError("Model plan exceeds the available search/read/final steps.")
    goal_anchors = _explicit_goal_anchors(user_query)
    for subgoal in plan.subgoals:
        if not subgoal.depends_on:
            for root_question in (subgoal.question, subgoal.alternate_question):
                    if root_question is None:
                        continue
                    root_anchors = _explicit_goal_anchors(root_question)
                    normalized_goal = _normalize_anchor_scope(user_query)
                    if (any(_normalize_anchor_scope(anchor) not in normalized_goal
                            for anchor in root_anchors)
                            or (goal_anchors and not root_anchors)):
                        raise ValidationError("Model plan root entity is not bound to the user goal.")
        questions = (subgoal.question, subgoal.alternate_question)
        for question in questions:
            if question is None:
                continue
            # "direct report of X" denotes X's subordinate, whereas "who
            # does X report to" denotes X's leader. Do not pick a direction
            # from competing proposed phrasings.
            if re.search(
                r"\bdirect report of\s+\{verified_entity\}",
                question, re.IGNORECASE,
            ):
                raise ValidationError(
                    "Model plan relation direction is ambiguous.",
                    details={"reason": "relation_direction"},
                )
            placeholders = re.findall(r"\{[^{}]*\}", question)
            if any(value != "{verified_entity}" for value in placeholders):
                raise ValidationError("Model plan contains an unapproved placeholder.")
            if (re.search(r"https?://|\b(?:email|send|write|delete|rename|move|upload|download|gold)\b|@",
                          question, re.IGNORECASE)
                    or re.search(
                        r"\b(?:another|other|different|external|foreign|private|cross[-\s]*)\s*workspace\b"
                        r"|\b(?:switch|access)\s+(?:to\s+)?(?:the\s+)?workspace\b"
                        r"|\bworkspace\s+(?:outside|other than)\b",
                        question, re.IGNORECASE,
                    )
                    or re.search(r"(?:发邮件|发送|删除|改写|上传|下载|移动文件|跨工作区)", question)):
                raise ValidationError("Model plan requested an action outside read-only retrieval.")
            if subgoal.depends_on and "{verified_entity}" not in question:
                raise ValidationError("Dependent model questions must use a verified entity placeholder.")
            if not subgoal.depends_on and "{verified_entity}" in question:
                raise ValidationError("A root model question cannot claim a verified entity.")
    return plan


def _explicit_goal_anchors(text: str) -> tuple[str, ...]:
    """Find literal named anchors; this is a scope check, not a query planner."""
    anchors = [
        " ".join(match.group().split()).casefold()
        for match in re.finditer(
            r"\b[A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*)+\b", text,
        )
    ]
    anchors.extend(
        match.group().casefold()
        for match in re.finditer(r"\b[A-Z]{2,}[A-Za-z0-9]*-[A-Za-z0-9]+\b", text)
    )
    for match in re.finditer(r"计划|项目|方案", text):
        prefix = text[max(0, match.start() - 2):match.start()]
        context = text[max(0, match.start() - 4):match.start()].rstrip()
        if (len(prefix) == 2
                and all("\u3400" <= char <= "\u9fff" for char in prefix)
                and prefix not in {"哪个", "什么", "这个", "这项", "该项", "此项", "哪项"}
                and "的" not in prefix
                and not re.search(r"(?:哪个|什么|接棒|交接|替代|取代|接替)$", context)):
            anchors.append((prefix + match.group()).casefold())
    return tuple(dict.fromkeys(anchors))


def _normalize_anchor_scope(text: str) -> str:
    """Compare literal scope anchors while treating punctuation as separators."""
    return " ".join(re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", text.casefold()).split())


def _passage_identity(
    value: dict[str, Any], expected_workspace_id: str,
) -> tuple[Any, ...] | None:
    """Validate an immutable passage against the active workspace before pairing."""
    path = value.get("relative_path")
    quote = value.get("quote")
    quote_hash = value.get("quote_sha256")
    if (not isinstance(path, str) or not path
            or not isinstance(quote, str) or not quote
            or value.get("workspace_id", expected_workspace_id) != expected_workspace_id
            or hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_hash):
        return None
    expected_id = passage_evidence_id(
        workspace_id=expected_workspace_id,
        resource_id=path, document_id=path, logical_path=f"/{path}",
        source_ref=path, content_hash=value["content_sha256"],
        line_start=value["line_start"], line_end=value["line_end"],
        quote_hash=quote_hash,
    )
    if value.get("evidence_id") != expected_id:
        return None
    return (expected_workspace_id, path, expected_id, value["content_sha256"],
            value["line_start"], value["line_end"], quote_hash)


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


def _verified_discovery_entity(
    question: str, evidence_values: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    """Bind an untyped document-discovery root to one literal named anchor."""
    values = tuple(evidence_values)
    anchors = {
        anchor for anchor in _explicit_goal_anchors(question)
        if any(anchor in " ".join(str(value["quote"]).casefold().split()) for value in values)
    }
    return tuple(anchors) if len(anchors) == 1 else ()


def verified_plan_subgoal_values(
    plan: TaskPlan, subgoal_id: str,
    evidence_by_subgoal: dict[str, tuple[dict[str, Any], ...]],
) -> tuple[str, ...] | None:
    """Recompute one subgoal object from its actual verified reads."""
    subgoal = plan.get(subgoal_id)
    values = evidence_by_subgoal[subgoal_id]
    if subgoal.relation_path is None:
        return verified_subgoal_values(subgoal.question, values)
    if not subgoal.relation_path:
        return _verified_discovery_entity(subgoal.question, values)
    scope = _plan_relation_scope(plan, subgoal, evidence_by_subgoal)
    if scope is None:
        return ()
    return verified_relation_path_values(
        subgoal.question, subgoal.relation_path, values,
        expected_subject=scope[0], expected_project_scope=scope[1], minimum_valid_on=scope[2],
    )


def _plan_relation_scope(plan, subgoal, evidence_by_subgoal):
    """Share the same parent, project and date binding for values and conflicts."""
    expected_subject = None
    expected_project_scope = None
    minimum_valid_on = None
    if subgoal.depends_on:
        if len(subgoal.depends_on) != 1:
            return None
        parent_id = subgoal.depends_on[0]
        parent_values = verified_plan_subgoal_values(plan, parent_id, evidence_by_subgoal)
        if parent_values is None or len(parent_values) != 1:
            return None
        expected_subject = parent_values[0]
        parent = plan.get(parent_id)
        if subgoal.relation_path[0] == "reports_to" and parent.relation_path and parent.relation_path[-1] == "owner_of_project":
            project = None
            if parent.depends_on:
                upstream = verified_plan_subgoal_values(plan, parent.depends_on[0], evidence_by_subgoal)
                if upstream is None or len(upstream) != 1:
                    return None
                project = upstream[0]
            elif len(parent.relation_path) == 2:
                upstream = verified_relation_path_values(
                    parent.question, parent.relation_path[:1], evidence_by_subgoal[parent_id],
                )
                if len(upstream) != 1:
                    return None
                project = upstream[0]
            binding = verified_owner_source_binding(
                parent.question, evidence_by_subgoal[parent_id], expected_subject,
                expected_project=project,
            )
            if binding is None:
                return None
            expected_project_scope, minimum_valid_on = binding
    return expected_subject, expected_project_scope, minimum_valid_on


def verified_plan_conflict_refs(plan, subgoal_id, evidence_by_subgoal):
    subgoal = plan.get(subgoal_id)
    if not subgoal.relation_path or len(subgoal.relation_path) != 1:
        return verified_subgoal_conflict_refs(subgoal.question, evidence_by_subgoal[subgoal_id])
    scope = _plan_relation_scope(plan, subgoal, evidence_by_subgoal)
    if scope is None:
        return ()
    relation = {"owner_of_project": "owner_of", "alias_of_project": "alias_of", "reports_to": "reports_to"}[subgoal.relation_path[0]]
    subject = scope[0] or _question_relation_entity(subgoal.question, relation)
    return verified_relation_conflict_refs(relation, subject, evidence_by_subgoal[subgoal_id],
        question=subgoal.question, expected_project_scope=scope[1], minimum_valid_on=scope[2]) if subject else ()


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


def assess_task_plan(
    plan: TaskPlan,
    records: Iterable[ToolExecutionRecord],
    *,
    run_id: str,
    task_id: str,
    agent_id: str,
    expected_workspace_id: str,
) -> PlanAssessment:
    """Pair each verified read with a preceding tagged search in this task."""
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
            for value in values:
                identity = _passage_identity(value, expected_workspace_id)
                if identity is not None:
                    found[subgoal_id] = True
                    pending_hits.setdefault(identity, []).append(subgoal_id)
                    prior_read = verified_reads.get(identity)
                    if prior_read is not None:
                        subgoal = plan.get(subgoal_id)
                        dependencies = source_context.get("dependency_entities", {})
                        supported = False
                        if not subgoal.depends_on and dependencies == {}:
                            if subgoal.relation_path is None:
                                supported = bool(verified_subgoal_values(subgoal.question, [prior_read]))
                            elif subgoal.relation_path:
                                supported = bool(verified_relation_path_values(
                                    subgoal.question, subgoal.relation_path, [prior_read],
                                ))
                            else:
                                supported = bool(_verified_discovery_entity(subgoal.question, [prior_read]))
                        elif len(subgoal.depends_on) == 1 and isinstance(dependencies, dict):
                            parent_id = subgoal.depends_on[0]
                            parent = plan.get(parent_id)
                            if parent.relation_path is None:
                                targets = verified_dependency_values(
                                    parent.question, subgoal.question, observed[parent_id],
                                )
                            else:
                                targets = verified_plan_subgoal_values(
                                    plan, parent_id,
                                    {key: tuple(rows) for key, rows in observed.items()},
                                )
                            if (targets is not None and len(targets) == 1
                                    and set(dependencies) == {parent_id}
                                    and isinstance(dependencies[parent_id], str)
                                    and " ".join(dependencies[parent_id].casefold().split()) == targets[0]):
                                if subgoal.relation_path is None:
                                    supported = bool(verified_subgoal_values(
                                        subgoal.question, [prior_read],
                                        expected_parent_entity=targets[0],
                                    )) or bool(verified_dependent_leader_values(
                                        parent.question, subgoal.question,
                                        observed[parent_id], [prior_read],
                                    ))
                                else:
                                    supported = bool(verified_relation_path_values(
                                        subgoal.question, subgoal.relation_path, [prior_read],
                                        expected_subject=targets[0],
                                    ))
                        if supported and all(
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
    """Find missing required hops using read evidence and typed parent relations.

    A read of an irrelevant search hit is not proof that its subgoal was answered.
    The narrower relation check is used only at the refusal acceptance boundary;
    material claims still pass the existing independent citation/claim checks.
    """
    missing = set(assessment.missing_subgoals)
    for child in plan.subgoals:
        values = verified_plan_subgoal_values(
            plan, child.subgoal_id, assessment.evidence_by_subgoal,
        )
        if values is None and len(child.depends_on) == 1:
            parent_id = child.depends_on[0]
            values = verified_dependent_leader_values(
                plan.get(parent_id).question, child.question,
                assessment.evidence_by_subgoal[parent_id],
                assessment.evidence_by_subgoal[child.subgoal_id],
            )
        if values == ():
            missing.add(child.subgoal_id)
        for parent_id in child.depends_on:
            parent_values = assessment.evidence_by_subgoal[parent_id]
            if not parent_values:
                missing.add(parent_id)
                missing.add(child.subgoal_id)
                continue
            parent = plan.get(parent_id)
            targets = (
                verified_plan_subgoal_values(plan, parent_id, assessment.evidence_by_subgoal)
                if parent.relation_path is not None else
                verified_dependency_values(parent.question, child.question, parent_values)
            )
            if targets == ():
                missing.add(parent_id)
                missing.add(child.subgoal_id)
    return tuple(item.subgoal_id for item in plan.subgoals if item.subgoal_id in missing)


def pending_alternate_subgoals(
    plan: TaskPlan, assessment: PlanAssessment, records: Iterable[ToolExecutionRecord],
    *, run_id: str, task_id: str, agent_id: str,
) -> tuple[str, ...]:
    """Return relation gaps with one completed primary and an unused planned alternate."""
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
        and not verified_plan_subgoal_values(
            plan, subgoal.subgoal_id, assessment.evidence_by_subgoal,
        )
    )


def conflicted_subgoals(plan: TaskPlan, assessment: PlanAssessment) -> tuple[str, ...]:
    return tuple(
        item.subgoal_id for item in plan.subgoals
        if verified_plan_conflict_refs(plan, item.subgoal_id, assessment.evidence_by_subgoal)
    )


def validate_relation_final(
    plan: TaskPlan, assessment: PlanAssessment, payload: dict[str, Any],
    evidence_by_ref: dict[str, dict[str, Any]], read_refs: Iterable[str],
) -> None:
    """Bind legacy source-relation Finals to this slice's directed plan/read set.

    This is deliberately scoped to the declared two/three-hop investigation,
    not a replacement for the existing general or temporal Final validators.
    """
    paths = tuple(item.relation_path for item in plan.subgoals)
    if plan.schema_version != 'task-plan/v2' or paths not in (
        (('owner_of_project',), ('reports_to',)),
        (('alias_of_project',), ('owner_of_project',), ('reports_to',)),
    ):
        return
    if set(payload['evidence_refs']) - set(read_refs):
        raise ValidationError('Planned Final cited evidence that was not read back.')
    for goal in plan.subgoals:
        opposing = verified_plan_conflict_refs(plan, goal.subgoal_id, assessment.evidence_by_subgoal)
        if opposing and not set(opposing) <= set(payload['uncertainty']['evidence_refs']):
            raise ValidationError('Conflicting relations require both opposing verified source citations.')
    if payload['actions'] or payload['unresolved_items'] or payload['rejected_alternatives']:
        raise ValidationError('Source-relation Final exceeds the declared relation path.')
    texts = [(item['point'], item['evidence_refs'], False) for item in payload['rationale']]
    main = payload['decision']['value']
    if main is not None:
        texts.append((main, payload['decision']['evidence_refs'], True))
    statement = payload['uncertainty']['statement']
    if statement is not None:
        texts.append((statement, payload['uncertainty']['evidence_refs'], False))
    main_slots: set[str] = set()
    for text, refs, is_main in texts:
        supported = assess_claim_text(text, refs, evidence_by_ref)
        if supported.status is not ClaimSupportStatus.SUPPORTED or not supported.claims:
            raise ValidationError('Planned Final contains an unsupported relation assertion.')
        for claim in supported.claims:
            matches = []
            for goal in plan.subgoals:
                targets = verified_plan_subgoal_values(plan, goal.subgoal_id, assessment.evidence_by_subgoal)
                source_relations = TYPED_CLAIM_RELATIONS[goal.relation_path[0]]['source_relations']
                if len(targets or ()) != 1 or claim.relation not in source_relations or claim.value != targets[0]:
                    continue
                if goal.depends_on:
                    parents = verified_plan_subgoal_values(plan, goal.depends_on[0], assessment.evidence_by_subgoal)
                    if len(parents or ()) != 1 or claim.subject != parents[0]:
                        continue
                else:
                    relation = 'alias_of' if goal.relation_path[0] == 'alias_of_project' else 'owner_of'
                    if claim.subject != _question_relation_entity(goal.question, relation):
                        continue
                dates = re.findall(r'\d{4}-\d{2}-\d{2}', goal.question)
                if dates and claim.valid_on != dates[0]:
                    continue
                matches.append(goal.subgoal_id)
            if not matches:
                raise ValidationError('Planned Final relation does not match a verified subgoal binding.')
            if is_main:
                main_slots.update(matches)
    if main is not None and main_slots != {item.subgoal_id for item in plan.subgoals}:
        raise ValidationError('Main Final does not cover the complete requested relation chain.')


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
        if not isinstance(entity, str) or not entity.strip() or len(entity) > 128:
            raise ValidationError("Dependency entity must be bounded text.")
        minimum_length = (
            2 if any("\u3400" <= char <= "\u9fff" for char in entity) else 3
        )
        if len(entity.strip()) < minimum_length:
            raise ValidationError("Dependency entity must be bounded text.")
        normalized = " ".join(entity.split()).casefold()
        parent_subgoal = plan.get(parent)
        expected_parent_entity = None
        if parent_subgoal.relation_path is not None:
            relation_targets = verified_plan_subgoal_values(
                plan, parent, assessment.evidence_by_subgoal,
            )
        else:
            relation_targets = None
        if relation_targets is None and len(parent_subgoal.depends_on) == 1:
            upstream_id = parent_subgoal.depends_on[0]
            upstream_targets = verified_dependency_values(
                plan.get(upstream_id).question,
                parent_subgoal.question,
                assessment.evidence_by_subgoal[upstream_id],
            )
            if upstream_targets is not None and len(upstream_targets) == 1:
                expected_parent_entity = upstream_targets[0]
        if relation_targets is None:
            relation_targets = verified_dependency_values(
                parent_subgoal.question,
                subgoal.question,
                assessment.evidence_by_subgoal[parent],
                expected_parent_entity=expected_parent_entity,
            )
        if relation_targets is not None:
            if normalized not in relation_targets:
                raise ValidationError(
                    "Dependency entity does not match the verified relation object."
                )
            entities.append(" ".join(entity.split()))
            continue
        entity_pattern = re.compile(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)")
        if normalized in {"the", "old", "new", "plan", "owner", "leader"} or not any(
            entity_pattern.search(" ".join(str(value["quote"]).split()).casefold())
            for value in assessment.evidence_by_subgoal[parent]
        ):
            raise ValidationError("Dependency entity was not found in verified parent evidence.")
        entities.append(" ".join(entity.split()))
    return tuple(entities)


__all__ = ["TaskPlan", "Subgoal", "PlanAssessment", "assess_task_plan",
           "refusal_missing_subgoals", "pending_alternate_subgoals", "conflicted_subgoals",
           "verified_dependency_entities", "validate_model_proposed_plan", "validate_relation_final"]
