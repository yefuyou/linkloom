"""Pure parse reuse must never reuse an authorization/currentness decision."""
from dataclasses import replace

import pytest

from linkloom.context.recovery import EvidenceRecovery, EvidenceRef
from linkloom.runtime.errors import ValidationError
from linkloom.semantic_ingestion import document_text
from tests.unit.test_runtime_context_sources import bridge_for


def count_parses(monkeypatch):
    original = document_text._parse_source_text
    calls = []

    def parse(artifact):
        calls.append(artifact)
        return original(artifact)

    monkeypatch.setattr(document_text, "_parse_source_text", parse)
    return calls


def test_each_immutable_document_is_parsed_once_per_snapshot(monkeypatch):
    bridge, context, _ = bridge_for("causality")
    expected = bridge.catalog()
    calls = count_parses(monkeypatch)
    for _ in range(2):
        before = len(calls)
        with bridge.session():
            assert bridge.catalog() == expected
            with bridge.session():
                assert bridge.catalog() == expected
                refs = tuple(EvidenceRef.from_dict(r["context_ref"]) for r in expected)
                recovered = EvidenceRecovery(bridge.read_source).restore(
                    workspace_id=context.workspace_id, refs=refs)
                assert recovered.status == "complete"
                assert [e.quote for e in recovered.evidence] == [s.quote for _, s in context.allowed_spans]
        assert len(calls) - before == len(context.artifacts)
    assert document_text._source_parses.get() is None


def test_cache_returns_exact_segments_and_does_not_outlive_exception(monkeypatch):
    _, context, _ = bridge_for()
    artifact = context.artifacts[0]
    expected = document_text.parse_source_text(artifact)
    calls = count_parses(monkeypatch)
    with pytest.raises(RuntimeError):
        with document_text.source_parse_session():
            first = document_text.parse_source_text(artifact)
            assert document_text.parse_source_text(artifact) is first
            assert first == expected
            raise RuntimeError("aborted snapshot")
    assert document_text._source_parses.get() is None
    assert document_text.parse_source_text(artifact) == expected
    assert len(calls) == 2


def test_equal_content_in_another_source_or_workspace_is_not_aliased(monkeypatch):
    _, context, _ = bridge_for()
    artifact = context.artifacts[0]
    variants = (artifact, replace(artifact), replace(artifact, workspace_id="other"),
                replace(artifact, artifact_id="other.md"), replace(artifact, content=artifact.content + "Changed."))
    calls = count_parses(monkeypatch)
    with document_text.source_parse_session():
        results = [document_text.parse_source_text(a) for a in variants]
        assert all(document_text.parse_source_text(a) is r for a, r in zip(variants, results))
    assert len(calls) == len(variants)
    assert results[0] == results[1]
    assert results[0].segments[0].evidence_span.evidence_ref != results[2].segments[0].evidence_span.evidence_ref
    assert results[0].segments[0].evidence_span.evidence_ref != results[3].segments[0].evidence_span.evidence_ref


@pytest.mark.parametrize("change", ["source", "permission", "span", "observation", "registry", "workspace"])
def test_snapshot_exit_rechecks_changes_even_after_cached_parse(change):
    bridge, context, live = bridge_for()
    original = live["snapshot"]
    with pytest.raises(ValidationError):
        with bridge.session():
            bridge.catalog()
            snapshot = live["snapshot"]
            if change == "source":
                snapshot = replace(snapshot, artifacts=(replace(snapshot.artifacts[0], content="Changed."), *snapshot.artifacts[1:]))
            elif change == "permission":
                snapshot = replace(snapshot, authorized_artifact_versions=frozenset())
            elif change == "span":
                snapshot = replace(snapshot, revoked_evidence_refs=frozenset([context.allowed_spans[0][1].evidence_ref]))
            elif change == "observation":
                snapshot = replace(snapshot, revoked_observation_ids=frozenset([context.observations[0].observation_id]))
            elif change == "workspace":
                snapshot = replace(snapshot, workspace_id="other")
            else:
                span = context.allowed_spans[0][1]
                snapshot.source_registry.register_evidence(context.workspace_id, span.evidence_ref,
                    content_hash=span.content_hash, source_version="changed")
            live["snapshot"] = snapshot
    assert bridge._captured is None and document_text._source_parses.get() is None
    with pytest.raises(ValidationError):
        bridge.catalog()
    if change != "registry":
        live["snapshot"] = original
        assert bridge.catalog()


def test_nested_parse_scopes_are_isolated_and_outer_scope_is_restored(monkeypatch):
    _, context, _ = bridge_for()
    artifact = context.artifacts[0]
    calls = count_parses(monkeypatch)
    with document_text.source_parse_session():
        outer = document_text.parse_source_text(artifact)
        with document_text.source_parse_session():
            inner = document_text.parse_source_text(artifact)
            assert inner == outer and inner is not outer
        assert document_text.parse_source_text(artifact) is outer
    assert len(calls) == 2


def test_new_source_version_recovers_new_text_and_rejects_old_refs():
    from linkloom.decision_memory.sources import SourceReferenceRegistry
    from linkloom.runtime.context_sources import TaskEvidenceBridge
    from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
    _, context, _ = bridge_for()
    artifact = context.artifacts[0]

    def snapshot(artifact):
        registry = SourceReferenceRegistry()
        for segment in document_text.parse_source_text(artifact).segments:
            span = segment.evidence_span
            registry.register_evidence(artifact.workspace_id, span.evidence_ref,
                content_hash=span.content_hash, source_version=span.artifact_version_id)
        return TaskContextSnapshot(artifact.workspace_id, (), (artifact,), registry)

    live = [snapshot(artifact)]
    bridge = TaskEvidenceBridge(TaskRelationTools(artifact.workspace_id, lambda: live[0]))
    old = bridge.catalog()
    refs = tuple(EvidenceRef.from_dict(row["context_ref"]) for row in old)
    changed = replace(artifact, content=artifact.content.replace("2026年12月10日", "2026年12月18日"))
    live[0] = snapshot(changed)
    new = bridge.catalog()
    assert new[0]["context_ref"]["content_hash"] == changed.content_hash != artifact.content_hash
    assert new[0]["context_ref"]["evidence_id"] != old[0]["context_ref"]["evidence_id"]
    recovery = EvidenceRecovery(bridge.read_source)
    assert not recovery.restore(workspace_id=artifact.workspace_id, refs=refs).evidence
    updated = recovery.restore(workspace_id=artifact.workspace_id,
        refs=tuple(EvidenceRef.from_dict(row["context_ref"]) for row in new))
    assert updated.status == "complete" and "2026年12月18日" in updated.evidence[0].quote
