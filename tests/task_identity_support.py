"""Public-input loader only. This module has no Gold path or expected verdict."""
from datetime import UTC, datetime
import json
from pathlib import Path

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation


INPUT_FILE = Path(__file__).parent / "fixtures" / "task_identity" / "inputs" / "dev.json"


def public_cases():
    data = json.loads(INPUT_FILE.read_text(encoding="utf8"))
    if set(data) != {"schema_version", "split", "cases"} or data["schema_version"] != "task-identity-input/v1" or data["split"] != "dev":
        raise ValueError("unsupported public identity input")
    return data["cases"]


def build_case(case):
    if set(case) != {"case_id", "workspace_id", "sources", "observations", "subject", "object", "predicate"} or case["predicate"] != "SAME_TASK":
        raise ValueError("unsupported or labelled candidate input")
    registry = SourceReferenceRegistry()
    artifacts = {source["artifact_id"]: RawArtifact(
        workspace_id=case["workspace_id"], **source, ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
        for source in case["sources"]}
    parsed = {name: parse_source_text(artifact).segments for name, artifact in artifacts.items()}
    for name, segments in parsed.items():
        for segment in segments:
            registry.register_evidence(case["workspace_id"], segment.evidence_span.evidence_ref,
                                      content_hash=artifacts[name].content_hash,
                                      source_version=artifacts[name].artifact_version_id)
    observations = {}
    for row in case["observations"]:
        artifact = artifacts[row["source_id"]]
        segment = parsed[row["source_id"]][row["segment_index"]]
        observation = build_task_observation(TaskProposal.from_dict(row["task"]), segment, artifact)
        if not observation.verify(artifact):
            raise ValueError("public observation failed M1 readback")
        observations[row["label"]] = observation
    return observations[case["subject"]], observations[case["object"]], tuple(artifacts.values()), registry
