from datetime import UTC, datetime

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.extraction import SemanticExtractionContext, _prompt
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text


def _semantic_prompt() -> str:
    artifact = RawArtifact(
        workspace_id="ws-prompt-contract",
        artifact_id="generic-semantic-contract",
        source_type="timestamped_text",
        content="[2026-10-01T09:00:00+00:00] Team: A generic statement.",
        ingestion_time=datetime(2026, 10, 1, tzinfo=UTC),
    )
    segment = parse_timestamped_text(artifact).segments[0]
    context = SemanticExtractionContext(
        expected_workspace_id=artifact.workspace_id,
        relation_resolver=FrozenRelationResolver(("uses vendor",)),
    )
    return _prompt(segment, context, "semantic-extraction/v1")


def test_prompt_defines_general_decision_fact_and_historical_reporting_boundaries() -> None:
    prompt = _semantic_prompt().casefold()

    assert "explicit team choice" in prompt
    assert "approval, adoption, switch, replacement, cancellation, or commitment" in prompt
    assert "does not itself represent a team choice or commitment" in prompt
    assert "replaces, supersedes, switches, adopts, approves, or changes a prior choice" in prompt
    assert "historical reporting alone" in prompt
    assert "a current state without decision language remains fact" in prompt
    assert "considering or proposing" in prompt
    assert "birchline" not in prompt
    assert "wrenwell" not in prompt
    assert "juniper receipts" not in prompt


def test_prompt_keeps_source_event_time_dates_owned_by_local_temporal_policy() -> None:
    prompt = _semantic_prompt().casefold()

    assert "for source_event_time, return null for valid_from and valid_to" in prompt
    assert "the local temporal policy derives the source event time" in prompt
