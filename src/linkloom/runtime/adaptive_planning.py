"""Product admission policy for model plans, stricter than general TaskPlan/v2."""
from __future__ import annotations

from datetime import date
import re

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.task_plan import TaskPlan, validate_model_proposed_plan


SOURCE_RELATION_PROFILE = "source-relations/v1"
_CHAINS = (("owner_of_project", "reports_to"),
           ("alias_of_project", "owner_of_project", "reports_to"))
_ROOT_QUESTIONS = {"owner_of_project": r"Who owns (.+)\?",
                   "alias_of_project": r"Which project does (.+) alias\?"}
_PRONOUNS = {"it", "this", "that", "this project", "the project", "the owner", "project",
             "这个项目", "该项目", "本项目", "项目", "它", "这位负责人", "负责人"}


def source_scope_violation(query: str) -> bool:
    """Reject explicit mutation or foreign-workspace requests, never reinterpret them."""
    return bool(re.search(
        r"\b(?:rename|delete|upload|download|email|send|save|write)\b|"
        r"\b(?:external|foreign|other|another|different|private)\s+workspace\b|"
        r"\bworkspace\s+(?:outside|beta|other than)\b|"
        r"跨工作区|其他工作区|外部工作区|别的工作区|删除|重命名|发送|上传|下载|改写|保存修改",
        query, re.I))


def validate_source_relation_plan(value, *, user_query: str, max_steps: int,
                                  as_of: str | None = None) -> TaskPlan:
    """Validate every proposal/repair before tools; never infer a missing edge."""
    plan = validate_model_proposed_plan(value, user_query=user_query, max_steps=max_steps)
    if source_scope_violation(user_query):
        raise ValidationError("The requested operation exceeds read-only current-workspace scope.")
    if plan.schema_version != "task-plan/v2":
        raise ValidationError("Source investigation requires TaskPlan/v2.")
    chain = tuple(goal.relation_path[0] if goal.relation_path and len(goal.relation_path) == 1
                  else "" for goal in plan.subgoals)
    if chain not in _CHAINS:
        raise ValidationError("Only project-owner-leader and alias-project-owner-leader are supported.")
    # A selected date is a host-owned scope. Otherwise accept only literal dates
    # from the goal; Product validates relative periods/ranges before admission.
    dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", user_query))
    dates.update(f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
                 for y, m, d in re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", user_query))
    if as_of:
        dates.add(as_of)
    if len(dates) > 1:
        raise ValidationError("Source plan has conflicting snapshot dates.")
    snapshot = next(iter(dates), None)
    if snapshot:
        try:
            date.fromisoformat(snapshot)
        except ValueError as error:
            raise ValidationError("Source plan date is invalid.") from error
    prefix = f"As of {snapshot} " if snapshot else ""
    for index, subgoal in enumerate(plan.subgoals):
        expected_parent = (plan.subgoals[index - 1].subgoal_id,) if index else ()
        if subgoal.depends_on != expected_parent or subgoal.alternate_question is not None:
            raise ValidationError("Source plans require one immediate parent and no alternate lookups.")
        if not subgoal.question.startswith(prefix):
            raise ValidationError("Source plan dropped its snapshot date.")
        question = subgoal.question[len(prefix):]
        if index == 0:
            match = re.fullmatch(_ROOT_QUESTIONS[chain[0]], question)
            if match is None:
                raise ValidationError("Source root question is not canonical.")
            entity = match[1].strip()
            if (not entity or len(entity) > 160 or entity.casefold() in _PRONOUNS
                    or re.search(r"[{}?？\n\r]", entity)
                    or re.search(r"(?<![a-z0-9])" + re.escape(entity.casefold()) + r"(?![a-z0-9])",
                                 user_query.casefold()) is None):
                raise ValidationError("Source root must name a literal entity in the user question.")
        else:
            expected = ("Who owns {verified_entity}?" if chain[index] == "owner_of_project"
                        else "Who is the direct leader of {verified_entity}?")
            if question != expected:
                raise ValidationError("Dependent source question must use its verified parent only.")
    return plan


RELATION_PLANNING_INSTRUCTIONS = """
Product profile source-relations/v1. Interpret unfamiliar Chinese, English and
mixed wording, synonyms and reordered clauses. You are a translator into TWO
supported directed paths ONLY: project -> owner -> direct leader (2 subgoals),
or alias -> project -> owner -> direct leader (3 subgoals).
Every relation_path has exactly ONE relation. No discovery roots, combined paths,
alternate_question, branches, multiple roots, joins or additional leadership hops.
The root entity MUST be copied literally from the trusted goal (preserve the full
name; do not substitute a known project for an alias). Never invent an entity.
Use EXACT canonical English questions:
  alias root: Which project does <literal alias> alias?
  project root: Who owns <literal project>?
  dependent owner: Who owns {verified_entity}?
  dependent leader: Who is the direct leader of {verified_entity}?
Each dependent depends_on is [the immediately preceding subgoal_id]. If an exact
date appears in the goal (including Chinese YYYY年M月D日), prefix EVERY question
with 'As of YYYY-MM-DD '. Keep the goal unchanged. Dates are snapshots, never a
range, relative period, history or continuity claim.
If the question lacks a named root, asks unrelated relationships, subordinates,
extra hops, multi-project comparisons, writes, cross-workspace access, or cannot
be faithfully expressed as one supported path, DECLINE by returning the exact
goal and schema_version task-plan/v2 with subgoals: []. This is a refusal signal;
it is not a valid executable TaskPlan. Never silently drop unsupported requests.
No documents, answers or tools are available while planning.
"""
