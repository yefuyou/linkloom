"""Public M3-F diagnostic: execute once (<=3 calls), replay, or score separately.

Run from the repository root with PYTHONPATH=src. Labels never enter execution.
Predictions and per-attempt request/response files are exclusive writes.
"""
from argparse import ArgumentParser
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from linkloom.agents.model_adapter import ModelResponse
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter, GEMINI_38_FLASH_MODEL_ID
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService

SOURCES = ROOT / "tests/fixtures/task_discovery/sources.json"
LABELS = ROOT / "tests/fixtures/task_discovery/labels.json"
IMPLEMENTATION = ROOT / "src/linkloom/semantic_ingestion/task_observation_discovery.py"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact_for(case):
    return RawArtifact(workspace_id="public-m3f", artifact_id=case["case_id"],
        source_type=case["source_type"], content=case["content"],
        ingestion_time=datetime(2029, 6, 3, tzinfo=UTC))


class RecordingProvider:
    def __init__(self, delegate, output, replay=None):
        self.delegate, self.output, self.replay, self.calls = delegate, output, replay, 0

    def complete(self, request):
        if self.calls >= 3:
            raise RuntimeError("THREE_CALL_LIMIT")
        self.calls += 1
        # Durable reservation before transmission; an interrupted output directory
        # cannot be reused by this runner. No SDK/extractor retries.
        save(self.output / f"request-{self.calls}.json", request.to_dict())
        if self.replay:
            original = read(self.replay / f"request-{self.calls}.json")
            if original != request.to_dict():
                raise RuntimeError("REPLAY_REQUEST_MISMATCH")
            response = ModelResponse.from_dict(read(self.replay / f"response-{self.calls}.json"))
        else:
            response = self.delegate.complete(request)
        save(self.output / f"response-{self.calls}.json", response.to_dict())
        return response


class UsageClient:
    def __init__(self, models, output):
        self.models, self.output, self.calls = models, output, 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls > 3:
            raise RuntimeError("THREE_GENERATION_LIMIT")
        try:
            response = self.models.generate_content(**kwargs)
            usage = getattr(response, "usage_metadata", None)
            save(self.output / f"sdk-usage-{self.calls}.json", {
                "actual_model_version": getattr(response, "model_version", None),
                "usage": {key: getattr(usage, key, None) for key in (
                    "prompt_token_count", "candidates_token_count", "thoughts_token_count", "total_token_count")},
            })
            return response
        except Exception as error:
            save(self.output / f"sdk-failure-{self.calls}.json", {"error_type": type(error).__name__})
            raise


def run(output, replay=None):
    output.mkdir(parents=True, exist_ok=False)
    cases = read(SOURCES)  # Deliberately does not read LABELS.
    save(output / "manifest.json", {"source_sha256": digest(SOURCES), "implementation_sha256": digest(IMPLEMENTATION),
        "extractor_sha256": digest(ROOT / "src/linkloom/semantic_ingestion/extraction.py"),
        "runner_sha256": digest(__file__), "model_id": GEMINI_38_FLASH_MODEL_ID,
        "max_calls": 3, "max_attempts": 1, "max_output_tokens": 3072,
        "labels_supplied_to_model": False, "scope": "NEW_PUBLIC_SYNTHETIC_DIAGNOSTIC"})
    sdk = None
    if replay:
        if read(replay / "manifest.json")["source_sha256"] != digest(SOURCES):
            raise RuntimeError("REPLAY_SOURCE_MISMATCH")
        provider = RecordingProvider(None, output, replay)
    else:
        try:
            from scripts.check_task_semantic_provider import connect
            sdk = connect()
        except Exception as error:
            save(output / "predictions.json", {"execution": "NOT_RUN", "reason": type(error).__name__, "calls": 0, "cases": []})
            return
        provider = RecordingProvider(GeminiProviderAdapter(UsageClient(sdk.models, output)), output)
    def guard(request):
        if len(request.user_input.encode("utf-8")) > 24000:
            raise ValueError("REQUEST_TOO_LARGE")
        return ProviderRequestAuthorization.approve(request, guard_id="m3f-diagnostic/v1",
            reservation_id="m3f-" + request.turn_id, estimated_cost_upper_bound_usd=.05)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="gemini", model_id=GEMINI_38_FLASH_MODEL_ID,
        request_guard=guard, request_guard_id="m3f-diagnostic/v1", max_attempts=1, max_output_tokens=3072)
    rows = []
    try:
        for case in cases:
            artifact = artifact_for(case)
            registry = SourceReferenceRegistry()
            for segment in parse_source_text(artifact).segments:
                registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                    content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
            service = TaskObservationDiscoveryService(extractor, source_registry=registry,
                current_source=lambda ws, identity: artifact)
            result = service.discover((artifact,), workspace_id=artifact.workspace_id)
            row = {"case_id": case["case_id"], "result": result.to_dict()}
            rows.append(row)
            save(output / (case["case_id"] + ".json"), row)
            print(json.dumps({"case_id": case["case_id"], "valid": len(result.valid_observations),
                              "partial": len(result.partial_observations),
                              "failures": [f.reason for f in result.failures]}, ensure_ascii=False), flush=True)
    finally:
        save(output / "predictions.json", {"execution": "REPLAY" if replay else "REAL_PROVIDER",
            "calls": provider.calls, "cases": rows, "model_id": GEMINI_38_FLASH_MODEL_ID,
            "cost_usd": None, "cost_note": "No invoice or current price lookup; .05 USD per-call admission estimate only."})
        if sdk:
            sdk.close()


def score(predictions, output):
    data, labels, cases = read(predictions), read(LABELS)["cases"], {c["case_id"]: c for c in read(SOURCES)}
    true_positive = predicted_count = correct_fields = field_count = valid_evidence = evidence_count = 0
    expected_count = sum(len(items) for items in labels.values())
    details, failures = [], []
    fields = ("owner", "project", "vendor", "deadline", "deadline_text", "deadline_precision")
    per_field = {field: {"correct": 0, "count": 0} for field in fields}
    for row in data["cases"]:
        expected = labels[row["case_id"]]
        result = row["result"]
        observations = {o["observation_id"]: o for o in result["valid_observations"] + result["partial_observations"]}
        artifact = artifact_for(cases[row["case_id"]])
        for observation in observations.values():
            evidence_count += 1
            valid_evidence += TaskObservation.from_dict(observation).verify(artifact)
        predictions_by_group = [list({oid: observations[oid] for oid in group["observation_ids"] if oid in observations}.values())
                                for group in result["groups"]]
        predictions_by_group = [items for items in predictions_by_group if items]
        matched = set()
        for items in predictions_by_group:
            predicted_count += 1
            descriptions = "；".join(o["description"] or "" for o in items)
            candidates = [i for i, target in enumerate(expected)
                          if all(work in descriptions for work in target["work"])]
            # A model group containing two independent labelled tasks is not
            # credited as one correct task merely because owners/dates match.
            match = candidates[0] if len(candidates) == 1 and candidates[0] not in matched else None
            if match is None:
                failures.append({"case_id": row["case_id"], "reason": "UNMATCHED_PREDICTED_TASK", "description": descriptions})
                continue
            matched.add(match)
            true_positive += 1
            for field in fields:
                # Union is evaluation-only: no cross-message field transplant in output.
                values = {o[field] for o in items if o[field] is not None and o[field] != "UNKNOWN"}
                actual = next(iter(values)) if len(values) == 1 else {"conflicting_values": sorted(values)} if values else None
                if field == "deadline_precision" and not values:
                    actual = "UNKNOWN"
                correct_fields += actual == expected[match][field]
                field_count += 1
                per_field[field]["correct"] += actual == expected[match][field]
                per_field[field]["count"] += 1
                if actual != expected[match][field]:
                    failures.append({"case_id": row["case_id"], "reason": "FIELD_MISMATCH", "field": field,
                                     "expected": expected[match][field], "actual": actual})
        for i, target in enumerate(expected):
            if i not in matched:
                failures.append({"case_id": row["case_id"], "reason": "MISSED_TASK", "work": target["work"]})
        failures.extend({"case_id": row["case_id"], **failure} for failure in result["failures"])
        details.append({"case_id": row["case_id"], "expected": len(expected), "predicted": len(predictions_by_group),
                        "matched": len(matched), "coverage_complete": result["coverage_complete"]})
    for case_id in labels.keys() - {row["case_id"] for row in data["cases"]}:
        failures.append({"case_id": case_id, "reason": "SOURCE_NOT_RUN"})
        details.append({"case_id": case_id, "expected": len(labels[case_id]), "predicted": 0,
                        "matched": 0, "coverage_complete": False})
    report = {"execution": data["execution"], "scope": "NEW_PUBLIC_SYNTHETIC_DIAGNOSTIC_NOT_HOLDOUT",
        "prediction_sha256": digest(predictions), "labels_sha256": digest(LABELS), "cases": details,
        "task_recall": true_positive / expected_count if expected_count else None,
        "task_precision": true_positive / predicted_count if predicted_count else None,
        "matched": true_positive, "expected": expected_count, "predicted": predicted_count,
        "field_accuracy_on_matched_tasks": correct_fields / field_count if field_count else None,
        "correct_fields": correct_fields, "field_count": field_count,
        "field_accuracy_by_field": {field: {**counts, "accuracy": counts["correct"] / counts["count"]
                                            if counts["count"] else None} for field, counts in per_field.items()},
        "description_accuracy": "Exact work-phrase coverage is required for one-to-one task matching.",
        "evidence_validity": valid_evidence / evidence_count if evidence_count else None,
        "valid_evidence": valid_evidence, "evidence_count": evidence_count, "failures": failures,
        "usage": [receipt for row in data["cases"] for receipt in row["result"]["receipts"]]}
    save(output, report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"usage", "failures"}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--replay", type=Path)
    mode.add_argument("--score", type=Path)
    args = parser.parse_args()
    if args.score:
        score(args.score, args.output)
    else:
        run(args.output, args.replay)
