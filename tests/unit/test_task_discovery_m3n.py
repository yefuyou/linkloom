"""M3-N optional shared-extractor input contracts, no real model inference."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    SemanticExtractionContext, TaskDiscoveryContext, TaskDiscoveryProfile, _prompt,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService, _window
from tests.m3n_contract_support import artifact_for, evaluate, load
from tests.unit.test_task_observation_discovery import claim, setup

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/requirements/m3-n-task-discovery/data"


def test_task_context_is_available_through_the_existing_public_extraction_api():
    from linkloom.semantic_ingestion import TaskDiscoveryContext as PublicContext
    from linkloom.semantic_ingestion import TaskDiscoveryProfile as PublicProfile
    assert PublicContext is TaskDiscoveryContext
    assert PublicProfile is TaskDiscoveryProfile


def test_optional_context_is_typed_immutable_and_source_bound_before_guard():
    artifact = artifact_for(load(DATA / "dev.json")["cases"][0])
    task = TaskDiscoveryContext(artifact)
    assert task.segments == parse_source_text(artifact).segments
    with pytest.raises(FrozenInstanceError):
        task.artifact = replace(artifact, content="other source")
    with pytest.raises(TypeError):
        TaskDiscoveryContext({"content": artifact.content})
    with pytest.raises(TypeError):
        SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(()), task_discovery={})
    with pytest.raises(TypeError):
        TaskDiscoveryContext(artifact, profile="cross-message-v1")
    service, provider, registry, _ = setup(artifact)
    with pytest.raises(TypeError):
        TaskObservationDiscoveryService(service.extractor, source_registry=registry,
            current_source=service.current_source, task_profile="cross-message-v1")
    context = SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(()), task_discovery=task)
    service.extractor._request_guard = lambda request: pytest.fail("mismatched context reached guard")
    foreign = replace(artifact, artifact_id="other-artifact")
    with pytest.raises(ValueError, match="window"):
        service.extractor.extract(_window(foreign), context)
    with pytest.raises(ValueError, match="window"):
        service.extractor.extract(task.segments[0], context)
    assert not provider.requests


def test_task_input_uses_unchanged_source_and_canonical_message_map():
    artifact = artifact_for(load(DATA / "dev.json")["cases"][0])
    service, provider, registry, _ = setup(artifact)
    original_segments = parse_source_text(artifact).segments
    task_service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
        current_source=service.current_source, task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1)
    task_service.discover((artifact,), workspace_id=artifact.workspace_id)
    request = provider.requests[0]
    instruction, payload = request.user_input.split("Input:\n", 1)
    data = json.loads(payload)
    assert data["source_segment"]["text"] == artifact.content
    assert data["task_discovery"]["profile"] == TaskDiscoveryProfile.CROSS_MESSAGE_V1.value
    assert data["task_discovery"]["context_version"] == "task-discovery-context/v1"
    for row, segment in zip(data["task_discovery"]["messages"], original_segments, strict=True):
        assert row["speaker"] == segment.speaker
        assert row["event_time"] == segment.event_time.isoformat()
        assert artifact.content[row["char_start"]:row["char_end"]] == segment.body_text
    for phrase in ("additional work", "every message", "same owner", "quoted", "conditional",
                   "discussing cancellation", "untrusted data", "separate evidence", "null"):
        assert phrase in instruction
    assert parse_source_text(artifact).segments == original_segments


def test_legacy_prompt_and_normalized_request_remain_byte_equivalent_to_baseline():
    # Compile only the frozen extractor in memory; no checkout/file rewrites.
    source = subprocess.run(["git", "show", "e757287:src/linkloom/semantic_ingestion/extraction.py"],
        cwd=ROOT, check=True, capture_output=True).stdout.decode("utf-8")
    spec = importlib.util.spec_from_loader("m3n_legacy_extraction", loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(source, "<e757287-extraction>", "exec"), module.__dict__)
    for case in load(DATA / "dev.json")["cases"]:
        artifact = artifact_for(case)
        service, provider, _, _ = setup(artifact)
        segments = parse_source_text(artifact).segments
        segment = segments[0] if len(segments) == 1 else _window(artifact)
        context = SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(()))
        old_context = module.SemanticExtractionContext(artifact.workspace_id, context.relation_resolver)
        assert _prompt(segment, context, "semantic-extraction/v1") == module._prompt(segment, old_context, "semantic-extraction/v1")
        extractor = service.extractor
        old = module.ProviderNeutralSemanticExtractor(provider, provider_id=extractor.provider_id,
            model_id=extractor.model_id, request_guard_id=extractor.request_guard_id,
            request_guard=lambda request: module.ProviderRequestAuthorization.approve(request,
                guard_id=extractor.request_guard_id, reservation_id="old", estimated_cost_upper_bound_usd=0))
        before = old.extract(segment, old_context)
        after = extractor.extract(segment, context)
        assert provider.requests[-2].to_dict() == provider.requests[-1].to_dict()
        assert before.receipt.config_sha256 == after.receipt.config_sha256
        assert before.receipt.extraction_fingerprint == after.receipt.extraction_fingerprint
        task_context = replace(context, task_discovery=TaskDiscoveryContext(artifact))
        new = extractor.extract(segment, task_context)
        repeat = extractor.extract(segment, task_context)
        assert new.receipt.prompt_sha256 != after.receipt.prompt_sha256
        assert new.receipt.config_sha256 != after.receipt.config_sha256
        assert new.receipt.extraction_fingerprint != after.receipt.extraction_fingerprint
        assert new.receipt.extraction_fingerprint == repeat.receipt.extraction_fingerprint
        assert provider.requests[-1].available_tools == provider.requests[-2].available_tools


def test_explicit_continuation_retains_per_message_evidence_and_no_field_migration():
    case = load(DATA / "dev.json")["cases"][0]
    artifact = artifact_for(case)
    first, second = case["expected"]
    service, provider, registry, _ = setup(artifact, [claim(artifact.content,
        *first["description_parts"], *second["description_parts"], owner=first["owner"],
        deadline_text=first["deadline_text"], project=first["project"], vendor=first["vendor"])])
    service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
        current_source=service.current_source, task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1)
    result = service.discover((artifact,), workspace_id=artifact.workspace_id)
    found = sorted(result.valid_observations + result.partial_observations,
                   key=lambda o: o.provenance.char_start)
    assert len(found) == 2 and len(result.groups) == 1
    assert len({o.provenance.evidence_ref for o in found}) == 2
    assert found[0].owner == first["owner"]
    assert found[1].owner is found[1].project is found[1].vendor is found[1].deadline is None
    assert all(o.verify(artifact) and o.relation_status == "UNCONFIRMED" for o in found)
    assert result.groups[0].status == "MODEL_PROPOSED"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("fault", ["stale", "revoked", "foreign", "during_call", "empty", "invented"])
def test_task_profile_preserves_source_and_evidence_boundaries(fault):
    case = load(DATA / "dev.json")["cases"][0]
    artifact = artifact_for(case)
    expected = case["expected"][0]
    proposals = [claim(expected["text"], *expected["description_parts"])]
    if fault == "empty":
        proposals = []
    elif fault == "invented":
        proposals = [claim(expected["text"], "invented unsupported work")]
    service, provider, registry, current = setup(artifact, proposals)
    service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
        current_source=service.current_source, task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1)
    if fault == "stale":
        current[0] = replace(artifact, content="updated content")
    elif fault == "revoked":
        registry.unregister_evidence(artifact.workspace_id, parse_source_text(artifact).segments[0].evidence_span.evidence_ref)
    elif fault == "foreign":
        artifact = replace(artifact, workspace_id="unapproved")
    elif fault == "during_call":
        provider.hook = lambda: current.__setitem__(0, replace(artifact, content="changed during call"))
    result = service.discover((artifact,), workspace_id="m3n-public")
    assert not result.valid_observations and not result.partial_observations
    assert len(provider.requests) == (0 if fault in {"stale", "revoked", "foreign"} else 1)


def test_task_context_rejects_incomplete_and_overlimit_input():
    artifact = artifact_for(load(DATA / "dev.json")["cases"][0])
    with pytest.raises(ValueError, match="ceilings"):
        TaskDiscoveryContext(replace(artifact, content="x" * 16001))
    with pytest.raises(ValueError, match="canonical"):
        TaskDiscoveryContext(replace(artifact, content="[2031-03-01Tbad] Team: work"))


def test_new_profile_cannot_promote_model_invented_fields_or_bypass_request_guard():
    artifact = artifact_for(load(DATA / "dev.json")["cases"][7])
    text = parse_source_text(artifact).segments[0].body_text
    service, provider, registry, _ = setup(artifact, [claim(text, "补录访问样例",
        owner="Not assigned", project="Not stated", vendor="Not allowed", deadline_text="2032-01-01")])
    service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
        current_source=service.current_source, task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1)
    result = service.discover((artifact,), workspace_id=artifact.workspace_id)
    observation, = result.partial_observations
    assert observation.owner is observation.project is observation.vendor is observation.deadline is None
    assert observation.verify(artifact)
    service.extractor._request_guard = lambda request: None
    blocked = service.discover((artifact,), workspace_id=artifact.workspace_id)
    assert len(provider.requests) == 1
    assert not blocked.valid_observations and not blocked.partial_observations
    assert blocked.failures[0].reason == "PROVIDER_REQUEST_BLOCKED"


@pytest.mark.parametrize("name", ["dev", "heldout"])
def test_frozen_public_oracle_contracts(name):
    manifest = load(DATA / "freeze_manifest.json")
    path = DATA / f"{name}.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["datasets"][name]["sha256"]
    report = evaluate(load(path), task_profile=TaskDiscoveryProfile.CROSS_MESSAGE_V1)
    totals = report["totals"]
    assert totals["recall"] == totals["precision"] == 1
    assert totals["field_accuracy"] == totals["evidence_validity"] == 1
    assert totals["erroneous_groups"] == totals["false_rejections"] == 0
    assert totals["calls"] == len(load(path)["cases"])
