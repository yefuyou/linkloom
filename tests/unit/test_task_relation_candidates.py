"""Independent synthetic discovery contracts; no fixed-pair Dev or heldout data."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_observation_query import TaskObservationRead
from linkloom.semantic_ingestion.task_relation_candidates import (
    DiscoveryLimits, RelationCandidate, discover_candidates, revalidate_candidate,
)


def observe(text, source="A.md", project="松澜项目", workspace="ws-discovery-unit", raw=None, **fields):
    artifact = RawArtifact(workspace, source, "manual_text", text,
                           ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
    observation = build_task_observation(TaskProposal(raw or text, (raw or text,), project=project, **fields),
                                         parse_source_text(artifact).segments[0], artifact)
    assert observation.verify(artifact)
    return artifact, observation


def registry_for(artifacts):
    registry = SourceReferenceRegistry()
    for artifact in artifacts:
        for segment in parse_source_text(artifact).segments:
            span = segment.evidence_span
            registry.register_evidence(span.workspace_id, span.evidence_ref,
                                      content_hash=span.content_hash, source_version=span.artifact_version_id)
    return registry


def discover(*items, **kwargs):
    artifacts = kwargs.pop("artifacts", tuple(x[0] for x in items))
    return discover_candidates("ws-discovery-unit", observations=kwargs.pop("observations", tuple(x[1] for x in items)),
                               artifacts=artifacts, source_registry=kwargs.pop("source_registry", registry_for(artifacts)),
                               **kwargs)


def test_scoped_keys_are_unconfirmed_candidates_with_exact_evidence():
    a = observe("松澜项目；系统：支付；任务 PAY-81：接入支付。")
    b = observe("松澜项目；系统：支付；任务 PAY-81：重做签名。", "B.md")
    before = [x[1].to_dict() for x in (a, b)]
    result = discover(a, b)
    candidates = [c for c in result.candidates if c.predicate_hint == "SAME_TASK"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert "SCOPED_TASK_KEY" in candidate.reason_codes
    assert candidate.to_dict()["relation_status"] == "UNCONFIRMED"
    assert candidate.to_dict()["phase"] == "DISCOVERED"
    assert "verdict" not in candidate.to_dict()
    assert "VERIFIER_REQUIRED" in candidate.uncertainty
    assert all(e.verify(next(x[0] for x in (a, b) if x[0].artifact_id == e.span.artifact_id)) for e in candidate.evidence)
    assert [x[1].to_dict() for x in (a, b)] == before
    assert [c.candidate_id for c in result.candidates] == [c.candidate_id for c in discover(b, a, a).candidates]


@pytest.mark.parametrize("text,project", [
    ("江岑项目；系统：支付；任务 PAY-81：接入支付。", "江岑项目"),
    ("松澜项目；系统：仓储；任务 PAY-81：接入支付。", "松澜项目"),
    ("任务 PAY-81：接入支付。", None),
])
def test_keys_cannot_escape_project_or_namespace(text, project):
    a = observe("松澜项目；系统：支付；任务 PAY-81：接入支付。")
    b = observe(text, "B.md", project)
    assert not any(c.predicate_hint == "SAME_TASK" for c in discover(a, b).candidates)


def test_explicit_reference_discovers_target_without_project_or_matching_description():
    a = observe("松澜项目任务 PAY-81：接入支付。")
    b = observe("处理回执。本任务与A.md的任务 PAY-81 有工作联系。", "B.md", None)
    candidates = discover(a, b).candidates
    assert any(c.predicate_hint == "RELATED_TO" and "EXPLICIT_FILE_TASK_REFERENCE" in c.reason_codes for c in candidates)


def test_stale_unverified_conflicting_and_foreign_observations_are_excluded():
    a = observe("松澜项目任务 PAY-81：接入支付。")
    b = observe("松澜项目任务 PAY-81：重做签名。", "B.md")
    foreign = observe(b[0].content, "F.md", workspace="foreign")
    for item in (replace(b[1], description="编造描述"),
                 TaskObservationRead(b[1], ("c1",), "NOT_CURRENT"),
                 TaskObservationRead(b[1], ("c1",), "CURRENT", True), foreign[1]):
        result = discover(a, b, foreign, observations=(a[1], item))
        assert not result.candidates
        assert result.rejected
    registry = registry_for((a[0], b[0]))
    registry.unregister_evidence(b[0].workspace_id, b[1].provenance.evidence_ref)
    assert not discover(a, b, source_registry=registry).candidates


def test_reimport_time_does_not_change_candidate_identity():
    a = observe("松澜项目任务 PAY-81：接入支付。")
    b = observe("松澜项目任务 PAY-81：重做签名。", "B.md")
    later = replace(a[1], recorded_at=a[1].recorded_at + timedelta(days=1))
    assert discover(a, b).candidates == discover(a, b, observations=(later, b[1])).candidates


def test_current_query_rows_are_accepted_but_payloads_still_verified():
    a = observe("松澜项目任务 PAY-81：接入支付。")
    b = observe("松澜项目任务 PAY-81：重做签名。", "B.md")
    rows = tuple(TaskObservationRead(x[1], ("capture-1",), "CURRENT") for x in (a, b))
    assert discover(a, b, observations=rows).candidates == discover(a, b).candidates


def test_direct_alias_dependency_is_scoped_and_versioned():
    a = observe("项目：银杏；任务 PAY-81：支付签名适配。", project="银杏")
    b = observe("松澜项目任务 PAY-81：支付签名迁移。", "B.md")
    alias = RawArtifact("ws-discovery-unit", "aliases.md", "manual_text",
                        '本工作区的“银杏”是“松澜项目”的内部代号。')
    artifacts = (a[0], b[0], alias)
    registry = registry_for(artifacts)
    candidates = discover(a, b, artifacts=artifacts, source_registry=registry).candidates
    candidate = next(c for c in candidates if c.predicate_hint == "SAME_TASK")
    assert any(e.span.artifact_id == "aliases.md" for e in candidate.evidence)
    arguments = dict(observations=(a[1], b[1]), artifacts=artifacts, source_registry=registry)
    assert revalidate_candidate(candidate, "ws-discovery-unit", **arguments)
    registry.unregister_evidence(alias.workspace_id, parse_source_text(alias).segments[0].evidence_span.evidence_ref)
    assert not revalidate_candidate(candidate, "ws-discovery-unit", **arguments)
    scoped = replace(alias, content='本工作区的“银杏”是“松澜项目”的内部代号，两者在本期计费资料中指同一项目。')
    assert not discover(a, b, artifacts=(a[0], b[0], scoped)).candidates


def test_supplier_change_is_a_typed_claim_lead_never_a_confirmed_impact():
    a = observe("松澜项目；供应商：北辰服务；负责支付签名适配。", vendor="北辰服务")
    change = RawArtifact("ws-discovery-unit", "change.md", "manual_text", "松澜项目供应商由南溪服务改为北辰服务。")
    candidates = discover(a, artifacts=(a[0], change)).candidates
    candidate = next(c for c in candidates if c.predicate_hint == "AFFECTED_BY")
    assert candidate.object.kind == "SOURCE_CLAIM"
    assert candidate.object.after_vendor == "北辰服务"
    assert {"DIRECT_IMPACT_UNPROVEN", "DELAY_UNPROVEN"} <= set(candidate.uncertainty)
    arguments = dict(observations=(a[1],), artifacts=(a[0], replace(change, content=change.content + "更新。")),
                     source_registry=registry_for((a[0], change)))
    assert not revalidate_candidate(candidate, "ws-discovery-unit", **arguments)


def test_only_reference_revision_cue_produces_directional_revision_hint():
    a = observe("松澜项目任务 PAY-81：支付适配；截止日期：2027-03-01。", deadline_text="2027-03-01")
    b = observe("松澜项目任务 PAY-81：支付适配；截止日期：2027-03-05。", "B.md", deadline_text="2027-03-05")
    assert not any(c.predicate_hint == "REVISES_PLAN" for c in discover(a, b).candidates)
    c = observe("松澜项目；本任务修订A.md的任务 PAY-81 的计划，支付适配截止日期：2027-03-05。",
                "C.md", deadline_text="2027-03-05")
    revision = next(c for c in discover(a, c).candidates if c.predicate_hint == "REVISES_PLAN")
    assert revision.subject.observation_id == c[1].observation_id
    assert revision.object.observation_id == a[1].observation_id


def test_candidate_revalidation_rejects_updated_endpoint_and_same_location_payload_changes():
    a = observe("松澜项目任务 PAY-81：支付签名适配。")
    b = observe("松澜项目任务 PAY-81：支付签名迁移。", "B.md")
    candidate = discover(a, b).candidates[0]
    args = dict(observations=(a[1], b[1]), artifacts=(a[0], b[0]), source_registry=registry_for((a[0], b[0])))
    assert revalidate_candidate(candidate, "ws-discovery-unit", **args)
    assert not revalidate_candidate(candidate, "foreign", **args)
    assert not revalidate_candidate(candidate, "ws-discovery-unit", **{**args, "artifacts": (replace(a[0], content=a[0].content + "更正。"), b[0])})
    changed = build_task_observation(TaskProposal(a[1].raw_text, ("支付签名",), project="松澜项目"),
                                     parse_source_text(a[0]).segments[0], a[0])
    assert not revalidate_candidate(candidate, "ws-discovery-unit", **{**args, "observations": (changed, b[1])})
    assert not discover(a, b, observations=(a[1], changed, b[1])).candidates


def test_limits_deduplication_and_ordering_are_visible_and_deterministic():
    items = [observe(f"松澜项目任务 PAY-81：适配第{i}个输出。", f"{i}.md") for i in range(6)]
    result = discover(*items, limits=DiscoveryLimits(max_candidates=3, max_per_observation=1))
    assert len(result.candidates) == 3 and result.truncated
    endpoints = [r.observation_id for c in result.candidates for r in (c.subject, c.object)]
    assert len(endpoints) == len(set(endpoints))
    assert result.candidates == discover(*reversed(items), limits=result.limits).candidates
    with pytest.raises(ValueError, match="snapshot"):
        discover(*items, limits=DiscoveryLimits(max_observations=2))
    with pytest.raises(ValueError, match="character"):
        discover(*items, limits=DiscoveryLimits(max_source_chars=10))
    with pytest.raises(ValueError):
        DiscoveryLimits(max_candidates=True)


def test_weak_project_and_lexical_candidates_are_explicitly_uncertain():
    a = observe("松澜项目；完成支付签名适配。")
    b = observe("松澜项目；支付签名验收。", "B.md")
    candidate = discover(a, b).candidates[0]
    assert candidate.predicate_hint == "RELATED_TO"
    assert "LEXICAL_OVERLAP_WITHIN_PROJECT" in candidate.reason_codes
    assert "PROJECT_COOCCURRENCE_ONLY" in candidate.uncertainty


def test_file_key_reference_selects_only_target_task_inside_multitask_source():
    first, second = "松澜项目任务 PAY-81：签名适配。", "松澜项目任务 PAY-82：重试适配。"
    a = observe(first + "\n" + second, raw=first)
    other = observe(a[0].content, raw=second)
    b = observe("处理回执；本任务与A.md的任务 PAY-81 有工作联系。", "B.md", None)
    candidates = discover(a, other, b).candidates
    assert any(c.predicate_hint == "RELATED_TO" and {c.subject.observation_id, c.object.observation_id} ==
               {a[1].observation_id, b[1].observation_id} for c in candidates)
    assert not any(c.subject.observation_id == other[1].observation_id or c.object.observation_id == other[1].observation_id
                   for c in candidates if "EXPLICIT_FILE_TASK_REFERENCE" in c.reason_codes)


def test_context_signal_budget_is_bounded_before_candidate_expansion():
    a = observe("松澜项目；供应商：北辰服务；负责适配。", vendor="北辰服务")
    changes = RawArtifact("ws-discovery-unit", "changes.md", "manual_text",
                          "\n".join(f"松澜项目供应商由南溪{i}改为北辰服务。" for i in range(130)))
    with pytest.raises(ValueError, match="context signal"):
        discover(a, artifacts=(a[0], changes))


def test_portable_candidate_roundtrip_rejects_tampering():
    a = observe("松澜项目任务 PAY-81：支付签名适配。")
    b = observe("松澜项目任务 PAY-81：支付签名迁移。", "B.md")
    for candidate in discover(a, b).candidates:
        data = candidate.to_dict()
        assert RelationCandidate.from_dict(data) == candidate
        data["authority"] = "AUTHORITATIVE"
        with pytest.raises(ValueError):
            RelationCandidate.from_dict(data)
    change = RawArtifact("ws-discovery-unit", "change.md", "manual_text", "松澜项目供应商由南溪服务改为北辰服务。")
    a = observe("松澜项目；供应商：北辰服务；负责适配。", vendor="北辰服务")
    candidate = discover(a, artifacts=(a[0], change)).candidates[0]
    assert RelationCandidate.from_dict(candidate.to_dict()) == candidate


def test_unbound_namespace_in_another_message_does_not_become_shared_scope():
    raw = "松澜项目任务 PAY-81：支付签名适配。"
    artifact = RawArtifact("ws-discovery-unit", "A.txt", "timestamped_text",
        "[2026-10-01T08:00:00Z] 张青: 系统：支付\n[2026-10-01T09:00:00Z] 张青: " + raw)
    observation = build_task_observation(TaskProposal(raw, (raw,), project="松澜项目"),
                                         parse_source_text(artifact).segments[1], artifact)
    a = (artifact, observation)
    b = observe("松澜项目任务 PAY-81：支付签名迁移。", "B.md")
    assert not any(c.predicate_hint == "SAME_TASK" for c in discover(a, b).candidates)


def test_ambiguous_file_references_are_marked_and_cannot_look_unique():
    a = observe("松澜项目任务 PAY-81：支付签名适配。", "team-a/A.md")
    other = observe("松澜项目任务 PAY-81：支付签名适配。", "team-b/A.md")
    b = observe("处理回执。本任务与A.md的任务 PAY-81 有工作联系。", "B.md", None)
    candidates = discover(a, other, b, anchor_ids=(b[1].observation_id,)).candidates
    assert len(candidates) == 2
    assert all("AMBIGUOUS_FILE_REFERENCE" in c.uncertainty for c in candidates)


def test_alias_project_can_find_supplier_change_without_asserting_impact():
    a = observe("项目：银杏；供应商：北辰服务；负责支付适配。", project="银杏", vendor="北辰服务")
    alias = RawArtifact("ws-discovery-unit", "aliases.md", "manual_text", '本工作区的“银杏”是“松澜项目”的内部代号。')
    change = RawArtifact("ws-discovery-unit", "change.md", "manual_text", "松澜项目的短信服务供应商从南溪服务改成北辰服务。")
    candidate = next(c for c in discover(a, artifacts=(a[0], alias, change)).candidates if c.predicate_hint == "AFFECTED_BY")
    assert candidate.object.project == "松澜项目"
    assert "ALIAS_SCOPE_REQUIRES_VERIFICATION" in candidate.uncertainty
    assert any(e.span.artifact_id == "aliases.md" for e in candidate.evidence)


def test_non_change_supplier_sentence_does_not_abort_other_discovery():
    a = observe("松澜项目任务 PAY-81；供应商：北辰服务；负责适配。", vendor="北辰服务")
    b = observe("松澜项目任务 PAY-81：负责验收。", "B.md")
    same = RawArtifact("ws-discovery-unit", "same.md", "manual_text", "松澜项目供应商由北辰服务改为北辰服务。")
    result = discover(a, b, artifacts=(a[0], b[0], same))
    assert any(c.predicate_hint == "SAME_TASK" for c in result.candidates)
    assert not any(c.predicate_hint == "AFFECTED_BY" for c in result.candidates)


def test_generation_budget_fails_explicitly_before_returning_partial_candidates():
    items = [observe(f"松澜项目；完成输出{i}。", f"{i}.md") for i in range(8)]
    limits = DiscoveryLimits(max_generated_candidates=10)
    for ordered in (items, list(reversed(items))):
        with pytest.raises(ValueError, match="candidate generation budget"):
            discover(*ordered, limits=limits)
    with pytest.raises(ValueError):
        DiscoveryLimits(max_generated_candidates=5001)
    with pytest.raises(ValueError):
        DiscoveryLimits(max_generated_candidates=True)


def test_change_candidates_share_the_same_generation_budget():
    a = observe("松澜项目；供应商：北辰服务；负责适配。", vendor="北辰服务")
    changes = RawArtifact("ws-discovery-unit", "changes.md", "manual_text",
        "\n".join(f"松澜项目供应商由南溪{i}改为北辰服务。" for i in range(4)))
    with pytest.raises(ValueError, match="candidate generation budget"):
        discover(a, artifacts=(a[0], changes), limits=DiscoveryLimits(max_generated_candidates=2))


def test_budget_usage_fanout_and_truncation_reasons_are_observable():
    items = [observe(f"松澜项目；完成输出{i}。", f"{i}.md") for i in range(4)]
    result = discover(*items, limits=DiscoveryLimits(max_candidates=1, max_per_observation=1))
    data = result.to_dict()
    assert data["input_counts"] == dict(observation_rows=4, artifact_rows=4,
        source_characters=sum(len(a.content) for a, _ in items), current_workspace_artifacts=4, context_signals=0)
    assert data["generated_candidates"] == data["considered_candidates"] == 6
    assert sum(data["endpoint_fanout"].values()) == 2
    assert data["max_endpoint_fanout"] == 1
    assert set(data["truncation_reasons"]) == {"MAX_CANDIDATES", "MAX_PER_OBSERVATION"}
    assert result.truncated


def test_multi_key_observation_is_marked_ambiguous_not_confirmed():
    a = observe("松澜项目任务 PAY-81：支付签名。任务 PAY-82：恢复备份。")
    b = observe("松澜项目任务 PAY-81：支付签名迁移。", "B.md")
    candidate = next(c for c in discover(a, b).candidates if c.predicate_hint == "SAME_TASK")
    assert "AMBIGUOUS_TASK_KEY" in candidate.uncertainty
