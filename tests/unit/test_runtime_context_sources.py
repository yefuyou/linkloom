from dataclasses import replace
import json

from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.context_sources import TaskEvidenceBridge
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.task_semantic_support import selected_context


def bridge_for(name="revision"):
    context = selected_context(name)
    live = {"snapshot": SemanticSourceSnapshot.from_context(context)}
    semantics = SemanticAnalysis("investigation", context.workspace_id, lambda: live["snapshot"], None,
        model_id="offline", policy=SemanticCallPolicy("FAKE"))
    tools = TaskRelationTools(context.workspace_id, lambda: TaskContextSnapshot(context.workspace_id,
        context.observations, context.artifacts, context.source_registry), semantic_analysis=semantics)
    return TaskEvidenceBridge(tools), context, live


def test_explicit_three_namespace_mapping_roundtrips_exact_unicode_quotes(tmp_path):
    bridge, context, _ = bridge_for()
    mapping = bridge.catalog()
    assert len(mapping) == len(context.allowed_spans)
    store = ModelArtifactStore(tmp_path)
    ref = store.write("mapping.json", {"entries": mapping}, kind="observation")
    loaded = store.read(ref.ref, expected_sha256=ref.sha256)["entries"]
    refs = tuple(EvidenceRef.from_dict(row["context_ref"]) for row in loaded)
    recovered = EvidenceRecovery(bridge.read_source).restore(workspace_id=context.workspace_id,
        refs=refs, required_ids=[r.evidence_id for r in refs])
    assert recovered.status == "complete"
    assert [r.quote for r in recovered.evidence] == [s.quote for _, s in context.allowed_spans]
    assert all(row["origin_ref"].startswith("evidence:") and row["runtime_passage"]["evidence_id"].startswith("ev_v2_")
               and row["context_ref"]["evidence_id"].startswith("context_ev_v1_") for row in loaded)


def test_recovery_rechecks_version_permission_revocation_and_span():
    bridge, context, live = bridge_for()
    refs = tuple(EvidenceRef.from_dict(row["context_ref"]) for row in bridge.catalog())
    recovery = EvidenceRecovery(bridge.read_source)
    original = live["snapshot"]
    variants = [replace(original, authorized_artifact_versions=frozenset()),
        replace(original, revoked_evidence_refs=frozenset([context.allowed_spans[0][1].evidence_ref])),
        replace(original, revoked_observation_ids=frozenset([context.observations[0].observation_id])),
        replace(original, artifacts=original.artifacts[1:])]
    for value in variants:
        live["snapshot"] = value
        assert recovery.restore(workspace_id=context.workspace_id, refs=refs, required_ids=[r.evidence_id for r in refs]).status == "incomplete"
    live["snapshot"] = original
    forged = replace(refs[0], char_start=refs[0].char_start + 1)
    assert recovery.restore(workspace_id=context.workspace_id, refs=[forged]).status == "incomplete"
    assert recovery.restore(workspace_id="wrong", refs=refs).status == "incomplete"


def test_stale_registry_cannot_restore_current_evidence():
    bridge, context, _ = bridge_for()
    refs = tuple(EvidenceRef.from_dict(row["context_ref"]) for row in bridge.catalog())
    span = context.allowed_spans[0][1]
    context.source_registry.register_evidence(context.workspace_id, span.evidence_ref,
        content_hash=span.content_hash, source_version="replacement-version")
    result = EvidenceRecovery(bridge.read_source).restore(workspace_id=context.workspace_id, refs=refs)
    assert result.status == "incomplete" and result.failures
