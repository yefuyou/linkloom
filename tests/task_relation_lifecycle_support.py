"""M2-P4 lifecycle acceptance support.

The support layer deliberately builds its inputs through the existing M1/P1,
P2, and P3 constructors.  It does not import evaluator labels into the
production path.  ``expected/dev.json`` is read only by the acceptance test and
the standalone evaluator after actual SQLite reads have completed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import (
    TaskProposal,
    TaskObservation,
    build_task_observation,
)
from linkloom.semantic_ingestion.task_relation_context import (
    assess_relation,
    source_context_quote,
)
from linkloom.semantic_ingestion.task_relation_models import (
    EvidenceQuote,
    ProjectRef,
    RelationAssessment,
)
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent
from linkloom.semantic_ingestion.task_relation_query import RelationQuery


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "tests/fixtures/task_relation_lifecycle/inputs/dev.json"
EXPECTED = ROOT / "tests/fixtures/task_relation_lifecycle/expected/dev.json"
REVISION_INPUT = ROOT / "tests/fixtures/task_revisions/inputs/dev.json"
P2_INPUT = ROOT / "tests/fixtures/task_relations_p2/inputs/dev.json"


@dataclass(frozen=True)
class BuiltCase:
    case: dict[str, Any]
    workspace_id: str
    artifacts: tuple[RawArtifact, ...]
    parsed: dict[str, Any]
    registry: SourceReferenceRegistry
    observations: dict[str, TaskObservation]


@dataclass(frozen=True)
class RevisionObjects:
    case: BuiltCase
    question: dict[str, Any]
    evidence: tuple[EvidenceQuote, ...]
    identity: RelationAssessment
    revision: RelationAssessment
    comparison: Any


@dataclass(frozen=True)
class P2Objects:
    case: BuiltCase
    assessments: dict[str, RelationAssessment]
    evidence: dict[str, tuple[EvidenceQuote, ...]]


def load_input() -> dict[str, Any]:
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    if data.get("schema_version") != "task-relation-lifecycle-input/v1" or data.get("split") != "dev":
        raise ValueError("invalid lifecycle public input")
    if not isinstance(data.get("cases"), list):
        raise ValueError("lifecycle input cases must be a list")
    return data


def load_expected() -> dict[str, Any]:
    data = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if data.get("schema_version") != "task-relation-lifecycle-expected/v1" or data.get("split") != "dev":
        raise ValueError("invalid lifecycle expected annotations")
    return data


def _source_cases(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {case["case_id"]: case for case in data["cases"]}


def _build_case(case: dict[str, Any], *, source_indexed: bool = False) -> BuiltCase:
    workspace = case["workspace_id"]
    artifacts: list[RawArtifact] = []
    parsed: dict[str, Any] = {}
    # Redwood intentionally has two immutable versions with the same artifact
    # identity.  Keep the list and source-index map instead of collapsing it.
    for index, source in enumerate(case["sources"]):
        artifact = RawArtifact(
            workspace_id=workspace,
            **source,
            ingestion_time=datetime(2026, 10, 18, tzinfo=UTC),
        )
        artifacts.append(artifact)
        parsed[f"{index}:{artifact.artifact_version_id}"] = parse_source_text(artifact)

    registry = SourceReferenceRegistry()
    for artifact in artifacts:
        result = parse_source_text(artifact)
        for segment in result.segments:
            registry.register_evidence(
                workspace,
                segment.evidence_span.evidence_ref,
                content_hash=artifact.content_hash,
                source_version=artifact.artifact_version_id,
            )

    observations: dict[str, TaskObservation] = {}
    for item in case["observations"]:
        if "source_index" in item:
            artifact = artifacts[item["source_index"]]
            result = parsed[f"{item['source_index']}:{artifact.artifact_version_id}"]
        else:
            source_id = item["source_id"]
            matches = [a for a in artifacts if a.artifact_id == source_id]
            if len(matches) != 1:
                raise ValueError(f"ambiguous source id in lifecycle case: {source_id}")
            artifact = matches[0]
            result = parsed[f"{artifacts.index(artifact)}:{artifact.artifact_version_id}"]
        observation = build_task_observation(
            TaskProposal.from_dict(item["task"]),
            result.segments[item["segment_index"]],
            artifact,
        )
        if not observation.verify(artifact):
            raise ValueError(f"M1 observation did not verify: {item['label']}")
        observations[item["label"]] = observation

    return BuiltCase(case, workspace, tuple(artifacts), parsed, registry, observations)


def _source_for_item(case: BuiltCase, item: dict[str, Any]) -> tuple[RawArtifact, Any]:
    if "source_index" in item:
        artifact = case.artifacts[item["source_index"]]
        parsed = case.parsed[f"{item['source_index']}:{artifact.artifact_version_id}"]
        return artifact, parsed
    source_id = item["source_id"]
    matches = [a for a in case.artifacts if a.artifact_id == source_id]
    if len(matches) != 1:
        raise ValueError(f"source id is not unique: {source_id}")
    artifact = matches[0]
    return artifact, case.parsed[f"{case.artifacts.index(artifact)}:{artifact.artifact_version_id}"]


def _evidence(case: BuiltCase, item: dict[str, Any], *, role: str = "RELATION_PROPOSAL") -> EvidenceQuote:
    artifact, parsed = _source_for_item(case, item)
    if item.get("segment_index") is None:
        quote = source_context_quote(artifact)
        case.registry.register_evidence(
            case.workspace_id,
            quote.span.evidence_ref,
            content_hash=artifact.content_hash,
            source_version=artifact.artifact_version_id,
        )
        span = quote.span
    else:
        span = parsed.segments[item["segment_index"]].evidence_span
    expected = item["quote"]
    if span.quote.count(expected) != 1:
        raise ValueError(f"ambiguous lifecycle evidence: {expected}")
    start = span.char_start + span.quote.index(expected)
    return EvidenceQuote(span, start, start + len(expected), expected, role)


def _revision_case(case_id: str) -> RevisionObjects:
    case = _source_cases(REVISION_INPUT)[case_id]
    built = _build_case(case)
    question = case["questions"][0] if case_id == "D07" else next(
        q for q in case["questions"] if q["question_id"] == "D04-b-revision"
    )
    evidence = tuple(_evidence(built, item) for item in question["evidence"])
    new = built.observations[question["subject"]]
    old = built.observations[question["object"]]
    args = dict(
        artifacts=built.artifacts,
        source_registry=built.registry,
        context_observations=tuple(built.observations.values()),
    )
    identity = assess_same_task(new, old, **args)
    revision = assess_plan_revision(new, old, same_task=identity, evidence=evidence, **args)
    comparison = compare_revision(new, old, same_task=identity, revision=revision, evidence=evidence, **args)
    return RevisionObjects(built, question, evidence, identity, revision, comparison)


def _p2_case(case_id: str) -> P2Objects:
    case = _source_cases(P2_INPUT)[case_id]
    built = _build_case(case)
    aliases: list[RelationAssessment] = []
    assessments: dict[str, RelationAssessment] = {}
    evidence_by_question: dict[str, tuple[EvidenceQuote, ...]] = {}

    def endpoint(data: dict[str, Any]):
        if data["type"] == "task":
            return built.observations[data["label"]]
        return ProjectRef(data["workspace_id"], data["name"])

    for question in case["questions"]:
        subject, object_ = endpoint(question["subject"]), endpoint(question["object"])
        evidence = tuple(_evidence(built, item) for item in question["evidence"])
        evidence_by_question[question["question_id"]] = evidence
        result = assess_relation(
            question["predicate"],
            subject,
            object_,
            artifacts=built.artifacts,
            source_registry=built.registry,
            evidence=evidence,
            context_observations=tuple(built.observations.values()),
            alias_assessments=tuple(aliases),
        )
        assessments[question["question_id"]] = result
        if result.predicate == "PROJECT_ALIAS":
            aliases.append(result)
    return P2Objects(built, assessments, evidence_by_question)


def _case_with_registry(built: BuiltCase, current_indexes: tuple[int, ...]) -> BuiltCase:
    """Build an explicit source snapshot for one lifecycle phase."""
    registry = SourceReferenceRegistry()
    for index in current_indexes:
        artifact = built.artifacts[index]
        parsed = built.parsed[f"{index}:{artifact.artifact_version_id}"]
        for segment in parsed.segments:
            registry.register_evidence(
                built.workspace_id,
                segment.evidence_span.evidence_ref,
                content_hash=artifact.content_hash,
                source_version=artifact.artifact_version_id,
            )
    return BuiltCase(
        built.case,
        built.workspace_id,
        built.artifacts,
        built.parsed,
        registry,
        built.observations,
    )


def _redwood_case() -> tuple[BuiltCase, BuiltCase, dict[str, RevisionObjects]]:
    case = next(c for c in load_input()["cases"] if c["case_id"] == "REDWOOD-version-retraction")
    all_versions = _build_case(case, source_indexed=True)
    # s0 contains only A/v1 and B/v1.  B/v2 is deliberately absent from the
    # current registry, even though its immutable artifact remains available
    # for later history/readback.
    s0_case = _case_with_registry(all_versions, (0, 1))
    a0, b0, b1 = (all_versions.observations[label] for label in ("A0", "B0", "B1"))

    def make(new: TaskObservation, old: TaskObservation, new_label: str, old_label: str) -> RevisionObjects:
        phase_case = s0_case if new_label == "B0" else s1_case
        new_item = next(i for i in case["observations"] if i["label"] == new_label)
        old_item = next(i for i in case["observations"] if i["label"] == old_label)
        # The exact source proposal is the original public input quote.  The
        # identity validator derives its proof from the observation payload;
        # P3 gets the two complete source statements as proposals.
        evidence = (
            EvidenceQuote(
                _source_for_item(phase_case, old_item)[1].segments[old_item["segment_index"]].evidence_span,
                _source_for_item(phase_case, old_item)[1].segments[old_item["segment_index"]].evidence_span.char_start,
                _source_for_item(phase_case, old_item)[1].segments[old_item["segment_index"]].evidence_span.char_end,
                old.raw_text,
                "RELATION_PROPOSAL",
            ),
            EvidenceQuote(
                _source_for_item(phase_case, new_item)[1].segments[new_item["segment_index"]].evidence_span,
                _source_for_item(phase_case, new_item)[1].segments[new_item["segment_index"]].evidence_span.char_start,
                _source_for_item(phase_case, new_item)[1].segments[new_item["segment_index"]].evidence_span.char_end,
                new.raw_text,
                "RELATION_PROPOSAL",
            ),
        )
        # Keep the snapshot inventory scoped to the two observations used by
        # this assessment.  Including the superseded B0 in the s1 proof would
        # make the new B1 relation depend on the old source version and would
        # correctly (but undesirably for this case) stale the replacement too.
        args = dict(artifacts=phase_case.artifacts, source_registry=phase_case.registry, context_observations=(new, old))
        identity = assess_same_task(new, old, **args)
        revision = assess_plan_revision(new, old, same_task=identity, evidence=evidence, **args)
        comparison = compare_revision(new, old, same_task=identity, revision=revision, evidence=evidence, **args)
        return RevisionObjects(phase_case, {"question_id": f"REDWOOD-{new_label}-revision", "subject": new_label, "object": old_label}, evidence, identity, revision, comparison)

    s0 = make(b0, a0, "B0", "A0")

    # s1 registers B/v2 and explicitly invalidates the old B/v1 locator by
    # pointing that locator at the new immutable source version.  It does not
    # rewrite either artifact or any stored assessment payload.
    s1_case = _case_with_registry(all_versions, (0, 1, 2))
    old_span = b0.provenance
    new_artifact = all_versions.artifacts[2]
    s1_case.registry.register_evidence(
        all_versions.workspace_id,
        old_span.evidence_ref,
        content_hash=new_artifact.content_hash,
        source_version=new_artifact.artifact_version_id,
    )
    s1 = make(b1, a0, "B1", "A0")

    return s0_case, s1_case, {
        "s0": s0,
        "s1": s1,
    }


def _relation_api():
    """Return the fixed P4 writer, event, and read-only query contracts."""
    return TaskRelationStore, RelationQuery, RelationEvent


def _put_assessment(store, assessment: RelationAssessment, evidence: tuple[EvidenceQuote, ...]):
    return store.put_assessment(assessment, evidence=evidence, replay_premise_ids=assessment.premise_ids)


def _append_retraction(store, event_cls, assessment: RelationAssessment, evidence=(), *, actor="dev-reviewer", reason="manual Dev lifecycle correction", recorded_at=None):
    previous = store.last_event(assessment.subject.workspace_id if assessment.subject else assessment.object.workspace_id, assessment.assessment_id)
    event_time = recorded_at or (
        previous.recorded_at + timedelta(seconds=1)
        if previous is not None
        else datetime.now(UTC)
    )
    event = event_cls.create(
        workspace_id=assessment.subject.workspace_id if assessment.subject else assessment.object.workspace_id,
        assessment_id=assessment.assessment_id,
        event_type="RETRACTED",
        reason=reason,
        actor=actor,
        previous_event_id=previous.event_id if previous else None,
        recorded_at=event_time,
        evidence=tuple(evidence),
    )
    return store.append_event(event)


def _query_snapshot(path, built: BuiltCase):
    _, query_cls, _ = _relation_api()
    return query_cls(
        path,
        artifacts=built.artifacts,
        source_registry=built.registry,
        observations=tuple(built.observations.values()),
    )


def _read(query, callback):
    with query as opened:
        return callback(opened)


def run_dev_lifecycle(tmp_path: Path) -> dict[str, Any]:
    """Run the complete Dev lifecycle sequence and return only actual reads."""
    store_cls, _, event_cls = _relation_api()
    db_path = Path(tmp_path) / "task-relation-lifecycle.sqlite3"
    d04 = _revision_case("D04")
    d07 = _revision_case("D07")
    d08 = _p2_case("D08")
    redwood_s0_case, redwood_s1_case, redwood_objects = _redwood_case()

    with store_cls(db_path) as store:
        # D04: a real +4 comparison is stored, then its identity premise is
        # manually retracted.  The comparison remains historical only.
        _put_assessment(store, d04.identity, d04.evidence)
        _put_assessment(store, d04.revision, d04.evidence)
        store.put_comparison(d04.comparison)
        d04_initial = _read(
            _query_snapshot(db_path, d04.case),
            lambda query: {
                "identity": query.get(d04.case.workspace_id, d04.identity.assessment_id, include_history=True).to_dict(),
                "revision": query.get(d04.case.workspace_id, d04.revision.assessment_id, include_history=True).to_dict(),
                "comparison": query.comparison(d04.case.workspace_id, d04.comparison.comparison_id).to_dict(),
            },
        )
        _append_retraction(store, event_cls, d04.identity, d04.evidence, reason="manual identity correction")
        d04_after = _read(
            _query_snapshot(db_path, d04.case),
            lambda query: {
                "identity": query.get(d04.case.workspace_id, d04.identity.assessment_id, include_history=True).to_dict(),
                "revision": query.get(d04.case.workspace_id, d04.revision.assessment_id, include_history=True).to_dict(),
                "comparison": query.comparison(d04.case.workspace_id, d04.comparison.comparison_id).to_dict(),
            },
        )

        # D07 is a real M1/P3 UNKNOWN, persisted without converting it into a
        # negative or a missing relation.
        _put_assessment(store, d07.identity, d07.evidence)
        _put_assessment(store, d07.revision, d07.evidence)
        store.put_comparison(d07.comparison)
        d07_read = _read(
            _query_snapshot(db_path, d07.case),
            lambda query: {
                "identity": query.get(d07.case.workspace_id, d07.identity.assessment_id).to_dict(),
                "revision": query.get(d07.case.workspace_id, d07.revision.assessment_id).to_dict(),
                "comparison": query.comparison(d07.case.workspace_id, d07.comparison.comparison_id).to_dict(),
            },
        )

        # D08: alias and derived project relation are stored separately.  The
        # derived relation remains historical after alias retraction and must
        # disappear from the default project projection.
        alias = d08.assessments["D08-project-alias"]
        derived = d08.assessments["D08-b3-in-project"]
        wrong_context = d08.assessments["D08-b6-in-project"]
        _put_assessment(store, alias, d08.evidence["D08-project-alias"])
        _put_assessment(store, derived, d08.evidence["D08-b3-in-project"])
        _put_assessment(store, wrong_context, d08.evidence["D08-b6-in-project"])
        d08_before = _read(
            _query_snapshot(db_path, d08.case),
            lambda query: {
                "project": [x.to_dict() for x in query.list(d08.case.workspace_id, project="星河项目")],
                "alias": query.get(d08.case.workspace_id, alias.assessment_id).to_dict(),
            },
        )
        _append_retraction(store, event_cls, alias, d08.evidence["D08-project-alias"], reason="manual alias correction")
        d08_after = _read(
            _query_snapshot(db_path, d08.case),
            lambda query: {
                "project": [x.to_dict() for x in query.list(d08.case.workspace_id, project="星河项目")],
                "history": [x.to_dict() for x in query.list(d08.case.workspace_id, project="星河项目", include_history=True)],
                "alias": query.get(d08.case.workspace_id, alias.assessment_id, include_history=True).to_dict(),
                "derived": query.get(d08.case.workspace_id, derived.assessment_id, include_history=True).to_dict(),
            },
        )

        # Redwood: the old immutable source version becomes stale when the
        # registry's old evidence ref is revalidated against the new version;
        # then the stale identity is explicitly retracted in s2.
        s0 = redwood_objects["s0"]
        s1 = redwood_objects["s1"]
        _put_assessment(store, s0.identity, s0.evidence)
        _put_assessment(store, s0.revision, s0.evidence)
        store.put_comparison(s0.comparison)
        redwood_s0 = _read(
            _query_snapshot(db_path, redwood_s0_case),
            lambda query: {
                "identity": query.get(redwood_s0_case.workspace_id, s0.identity.assessment_id, include_history=True).to_dict(),
                "revision": query.get(redwood_s0_case.workspace_id, s0.revision.assessment_id, include_history=True).to_dict(),
                "comparison": query.comparison(redwood_s0_case.workspace_id, s0.comparison.comparison_id).to_dict(),
            },
        )
        _put_assessment(store, s1.identity, s1.evidence)
        _put_assessment(store, s1.revision, s1.evidence)
        store.put_comparison(s1.comparison)
        redwood_s1 = _read(
            _query_snapshot(db_path, redwood_s1_case),
            lambda query: {
                "old_identity": query.get(redwood_s1_case.workspace_id, s0.identity.assessment_id, include_history=True).to_dict(),
                "old_revision": query.get(redwood_s1_case.workspace_id, s0.revision.assessment_id, include_history=True).to_dict(),
                "old_comparison": query.comparison(redwood_s1_case.workspace_id, s0.comparison.comparison_id).to_dict(),
                "new_identity": query.get(redwood_s1_case.workspace_id, s1.identity.assessment_id, include_history=True).to_dict(),
                "new_revision": query.get(redwood_s1_case.workspace_id, s1.revision.assessment_id, include_history=True).to_dict(),
                "new_comparison": query.comparison(redwood_s1_case.workspace_id, s1.comparison.comparison_id).to_dict(),
            },
        )
        _append_retraction(store, event_cls, s0.identity, s0.evidence, reason="manual Redwood identity correction")
        redwood_s2 = _read(
            _query_snapshot(db_path, redwood_s1_case),
            lambda query: {
                "old_identity": query.get(redwood_s1_case.workspace_id, s0.identity.assessment_id, include_history=True).to_dict(),
                "old_revision": query.get(redwood_s1_case.workspace_id, s0.revision.assessment_id, include_history=True).to_dict(),
                "old_comparison": query.comparison(redwood_s1_case.workspace_id, s0.comparison.comparison_id).to_dict(),
                "new_identity": query.get(redwood_s1_case.workspace_id, s1.identity.assessment_id, include_history=True).to_dict(),
            },
        )

    return {
        "db_path": str(db_path),
        "cases": {
            "D04-plus-four": {"initial": d04_initial, "after_retraction": d04_after},
            "D07-unknown": d07_read,
            "D08-derived-project": {"before_retraction": d08_before, "after_retraction": d08_after},
            "REDWOOD-version-retraction": {"s0": redwood_s0, "s1": redwood_s1, "s2": redwood_s2},
        },
        "real_sqlite": db_path.exists(),
        "provider_calls": 0,
        "holdout_executed": False,
    }
