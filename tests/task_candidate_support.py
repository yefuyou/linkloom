"""Public synthetic discovery input only. This module never opens scoring Gold."""
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_candidates import DiscoveryLimits, discover_candidates, revalidate_candidate
from linkloom.semantic_ingestion.task_relation_models import ObservationRef

INPUT = Path(__file__).parent / "fixtures/task_candidate_discovery/inputs/workspaces.json"


def public_cases():
    return json.loads(INPUT.read_text(encoding="utf8"))["cases"]


def load_case(case):
    artifacts, observations, labels = [], [], {}
    registry = SourceReferenceRegistry()
    for source in case["sources"]:
        artifact = RawArtifact(case["workspace_id"], source["id"], "manual_text", source["content"],
                               ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
        artifacts.append(artifact)
        segments = parse_source_text(artifact).segments
        for segment in segments:
            span = segment.evidence_span
            registry.register_evidence(artifact.workspace_id, span.evidence_ref, content_hash=artifact.content_hash,
                                       source_version=artifact.artifact_version_id)
        for task in source["tasks"]:
            proposal = TaskProposal(task.get("raw", source["content"]), (task["description"],),
                project=task.get("project"), vendor=task.get("vendor"), deadline_text=task.get("deadline_text"))
            observation = build_task_observation(proposal, segments[0], artifact)
            assert observation.verify(artifact) and observation.description is not None
            observations.append(observation)
            labels[observation.observation_id] = "O:" + task["id"]
    anchor = next(o for o in observations if labels[o.observation_id] == "O:" + case["anchor"])
    return tuple(artifacts), tuple(observations), registry, labels, anchor


def run_case(case, k=5):
    artifacts, observations, registry, labels, anchor = load_case(case)
    limits = DiscoveryLimits(max_candidates=k, max_per_observation=k)
    result = discover_candidates(case["workspace_id"], observations=observations, artifacts=artifacts,
                                source_registry=registry, limits=limits, anchor_ids=(anchor.observation_id,))
    rows = []
    for candidate in result.candidates:
        target = candidate.object if candidate.subject.observation_id == anchor.observation_id else candidate.subject
        label = labels[target.observation_id] if isinstance(target, ObservationRef) else "C:" + target.evidence.span.artifact_id
        rows.append(dict(target=label, predicate_hint=candidate.predicate_hint, candidate=candidate.to_dict()))
    # Transparent exact-description baseline with the same workspace/input/K.
    # It deliberately lacks project/key/alias/reference/change discovery signals.
    matches = sorted((o for o in observations if o.observation_id != anchor.observation_id and
                     normalize_temporal_component(o.description) == normalize_temporal_component(anchor.description)),
                     key=lambda o: o.observation_id)
    baseline = [dict(target=labels[o.observation_id], predicate_hint="RELATED_TO",
                     reason_codes=["EXACT_NORMALIZED_DESCRIPTION"]) for o in matches[:k]]
    return dict(case_id=case["case_id"], workspace_id=case["workspace_id"], anchor="O:" + case["anchor"],
                baseline=baseline, discovery=rows, diagnostics={k: v for k, v in result.to_dict().items() if k != "candidates"})


def version_trials():
    """Update one task, alias or change source without using any scoring answer."""
    selected = {"C01": "plan.md", "C03": "aliases.md", "C04": "vendor.md"}
    trials = []
    for case in public_cases():
        if case["case_id"] not in selected:
            continue
        artifacts, observations, registry, _, anchor = load_case(case)
        candidates = discover_candidates(case["workspace_id"], observations=observations, artifacts=artifacts,
            source_registry=registry, anchor_ids=(anchor.observation_id,)).candidates
        source_id = selected[case["case_id"]]
        candidate = next(c for c in candidates if any(e.span.artifact_id == source_id for e in c.evidence))
        arguments = dict(observations=observations, artifacts=artifacts, source_registry=registry)
        before = revalidate_candidate(candidate, case["workspace_id"], **arguments)
        source = next(a for a in artifacts if a.artifact_id == source_id)
        updated = replace(source, content=source.content + "\n来源已更新。")
        for segment in parse_source_text(source).segments:
            registry.unregister_evidence(source.workspace_id, segment.evidence_span.evidence_ref)
        for segment in parse_source_text(updated).segments:
            registry.register_evidence(updated.workspace_id, segment.evidence_span.evidence_ref,
                content_hash=updated.content_hash, source_version=updated.artifact_version_id)
        arguments["artifacts"] = tuple(updated if a.artifact_id == source_id else a for a in artifacts)
        after = revalidate_candidate(candidate, case["workspace_id"], **arguments)
        trials.append(dict(case_id=case["case_id"], updated_source=source_id, valid_before=before, valid_after=after,
                           old_version=source.artifact_version_id, new_version=updated.artifact_version_id))
    return trials


def five_line_safety_trial():
    from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
    from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
    from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
    content = (INPUT.parent / "original-five-lines.txt").read_text(encoding="utf8")
    artifact = RawArtifact("discovery-original-safety", "meeting.txt", "timestamped_text", content,
                           ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
    segments = parse_source_text(artifact).segments
    tasks = []
    for index, description, project, vendor, deadline in (
        (1, ("短信接口接入", "联调"), "星河项目", None, "2026年10月14日"),
        (3, ("短信接口接入", "按新供应商星帆通信的接口调整"), None, "星帆通信", "2026年10月18日"),
    ):
        proposal = TaskProposal(segments[index].evidence_span.quote, description, owner="陈晨", project=project,
                                vendor=vendor, deadline_text=deadline)
        tasks.append(build_task_observation(proposal, segments[index], artifact))
    registry = SourceReferenceRegistry()
    for segment in segments:
        registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
            content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    result = discover_candidates(artifact.workspace_id, observations=tuple(tasks), artifacts=(artifact,), source_registry=registry)
    args = dict(artifacts=(artifact,), source_registry=registry)
    same_task = assess_same_task(tasks[1], tasks[0], **args)
    revision = assess_plan_revision(tasks[1], tasks[0], same_task=same_task, **args)
    comparison = compare_revision(tasks[1], tasks[0], revision=revision, same_task=same_task, **args)
    return dict(discovery=result.to_dict(), observations=[o.to_dict() for o in tasks], same_task=same_task.verdict,
                revision=revision.verdict, comparison_status=comparison.status,
                delta_calendar_days=comparison.delta_calendar_days,
                boundary="Original public five-line safety probe; not an old heldout run or a Provider evaluation")
