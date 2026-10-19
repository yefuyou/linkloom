from dataclasses import replace

from linkloom.tools.semantic_context import SemanticSourceSnapshot, build_semantic_context
from tests.task_semantic_support import selected_context


def raw_snapshot(name="revision"):
    original = selected_context(name)
    return SemanticSourceSnapshot.from_context(original), original


def test_builds_complete_original_sources_without_using_display_projection():
    snapshot, original = raw_snapshot()
    result = build_semantic_context(snapshot, expected_workspace_id=original.workspace_id)
    assert result.errors == ()
    assert result.context.source_snapshot_hash == original.source_snapshot_hash
    assert result.context.observations == original.observations
    assert result.context.allowed_spans == original.allowed_spans


def test_missing_competitor_or_source_and_explicit_truncation_are_blocked():
    snapshot, original = raw_snapshot()
    for broken in (replace(snapshot, artifacts=snapshot.artifacts[:1]),
                   replace(snapshot, observations=snapshot.observations[:1]),
                   replace(snapshot, truncated=True), replace(snapshot, inventory_complete=False)):
        result = build_semantic_context(broken, expected_workspace_id=original.workspace_id)
        assert result.errors and not result.ready


def test_wrong_workspace_revocation_and_budget_fail_before_provider():
    snapshot, original = raw_snapshot()
    from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticLimits
    for broken in (replace(snapshot, workspace_id="foreign"),
        replace(snapshot, revoked_observation_ids=frozenset([snapshot.observations[0].observation_id])),
        replace(snapshot, limits=SemanticLimits(max_observations=1))):
        assert not build_semantic_context(broken, expected_workspace_id=original.workspace_id).ready
