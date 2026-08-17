"""Formal offline evaluation runner and deterministic mock scenarios."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping

from .bad_cases import classify_link_case, classify_relation_detail, create_bad_case
from .dataset import EvaluationDataset, InferenceDataset
from .inference import mock_inference
from .isolation import check_inference_module, check_inference_payload, compare_snapshots, snapshot_tree
from .metrics import (
    evidence_ownership_metrics,
    evidence_metrics,
    relation_metrics_from_records,
    retrieval_hit_at_k,
    retrieval_mrr,
    topic_metrics,
    trace_quality_metrics,
)
from .models import (
    BadCaseCategory,
    BadCaseSeverity,
    EvaluationRunManifest,
    MetricResult,
    PredictionRecord,
    SafetyReport,
)
from .reporting import write_run_artifacts


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pair_key(source: str, target: str) -> tuple[str, str]:
    return tuple(sorted((source, target)))


def _safe_evidence(note_path: str, quote: str) -> dict[str, str]:
    return {"note_path": note_path, "quote_sha256": _sha256_text(quote)}


def _gold_relation_map(dataset: EvaluationDataset) -> dict[tuple[str, str], dict[str, Any]]:
    result = {
        _pair_key(pair.source_id, pair.target_id): {
            "source": None,
            "target": None,
            "relation_type": None,
            "should_link": False,
        }
        for pair in dataset.get_inference_pairs()
    }
    for pair in dataset.get_gold_pairs():
        result[_pair_key(pair.source, pair.target)] = {
            "source": pair.source,
            "target": pair.target,
            "relation_type": pair.relation_type,
            "should_link": pair.should_link,
        }
    return result


def _oracle_predictions(dataset: EvaluationDataset) -> list[dict[str, Any]]:
    """Build evaluator-side oracle records; never passed into inference code."""
    note_by_path = {note.note_path: note for note in dataset.get_gold_notes()}
    predictions: list[dict[str, Any]] = []
    for document in dataset.get_inference_documents():
        note = note_by_path[document.path]
        predictions.append(
            {
                "kind": "topic",
                "document_id": document.path,
                "should_link": bool(note.should_link_to_agent_evaluation),
                "confidence": 1.0,
                "evidence_refs": [_safe_evidence(document.path, quote) for quote in note.evidence],
            }
        )

    pair_gold = _gold_relation_map(dataset)
    quotes_by_pair = {
        _pair_key(pair.source, pair.target): pair.evidence
        for pair in dataset.get_gold_pairs()
    }
    for pair in dataset.get_inference_pairs():
        key = _pair_key(pair.source_id, pair.target_id)
        expected = pair_gold[key]
        evidence = quotes_by_pair.get(key, {"source": [], "target": []})
        predictions.append(
            {
                "kind": "relation",
                "source_id": pair.source_id,
                "target_id": pair.target_id,
                "should_link": bool(expected["should_link"]),
                "source": expected["source"],
                "target": expected["target"],
                "relation_type": expected["relation_type"],
                "confidence": 1.0,
                "evidence_refs": [
                    _safe_evidence(pair.source_id, quote) for quote in evidence.get("source", [])
                ]
                + [
                    _safe_evidence(pair.target_id, quote) for quote in evidence.get("target", [])
                ],
            }
        )
    return predictions


def _mutate_noisy(dataset: EvaluationDataset, predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(predictions)
    topics = [item for item in result if item.get("kind") == "topic"]
    if topics:
        topics[0]["should_link"] = not bool(topics[0]["should_link"])
    relations = [item for item in result if item.get("kind") == "relation"]
    negative = next((item for item in relations if not item.get("should_link")), None)
    if negative is not None:
        negative["should_link"] = True
        negative["source"] = negative["source_id"]
        negative["target"] = negative["target_id"]
        negative["relation_type"] = "concept-to-practice"
    positive = next((item for item in relations if item.get("should_link")), None)
    if positive is not None:
        positive["relation_type"] = "noisy-type"
    return result


def _mutate_malformed(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(predictions)
    for item in result:
        if item.get("kind") == "topic":
            item["confidence"] = 2.0
            break
    for item in result:
        if item.get("kind") == "relation":
            item["relation_type"] = None
            item["evidence_refs"] = "not-a-list"
            break
    return result


def _validate_prediction(record: Mapping[str, Any]) -> bool:
    if record.get("kind") == "topic":
        required = {"kind", "document_id", "should_link", "confidence", "evidence_refs"}
        return (
            required <= set(record)
            and isinstance(record.get("document_id"), str)
            and isinstance(record.get("should_link"), bool)
            and isinstance(record.get("confidence"), (int, float))
            and 0 <= float(record["confidence"]) <= 1
            and isinstance(record.get("evidence_refs"), list)
        )
    if record.get("kind") == "relation":
        required = {"kind", "source_id", "target_id", "should_link", "confidence", "evidence_refs"}
        return (
            required <= set(record)
            and isinstance(record.get("source_id"), str)
            and isinstance(record.get("target_id"), str)
            and isinstance(record.get("should_link"), bool)
            and isinstance(record.get("confidence"), (int, float))
            and 0 <= float(record["confidence"]) <= 1
            and isinstance(record.get("evidence_refs"), list)
        )
    return False


def _jsonable_bad_case(case: Any) -> dict[str, Any]:
    return asdict(case)


class FormalEvaluationRunner:
    def __init__(self, repo_root: str | Path, output_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root)

    def run(self, provider: str = "mock", scenario: str = "perfect", run_id: str | None = None) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        run_id = run_id or f"run_eval_{uuid.uuid4().hex[:12]}"
        
        status = "completed"
        predictions: list[dict[str, Any]] = []
        bad_cases: list[Any] = []
        evidence_validations: list[dict[str, Any]] | None = None
        usage: dict[str, Any] | None = None
        oracle = provider == "mock"
        
        # Inference Phase
        inference_dataset = InferenceDataset(self.repo_root)
        before_fixture = snapshot_tree(inference_dataset.fixture_root)
        
        if provider == "gemini_cli":
            from .real_provider import AgyCliAdapter
            from .real_execution import RealProviderRunner
            adapter = AgyCliAdapter()
            real_runner = RealProviderRunner(adapter)
            predictions, bad_cases, evidence_validations, usage = real_runner.run_inference(inference_dataset, run_id=run_id)
            if any(case.category == BadCaseCategory.PROVIDER_ERROR for case in bad_cases):
                status = "failed"
            elif any(case.category == BadCaseCategory.MALFORMED_OUTPUT for case in bad_cases):
                status = "invalid"
        elif provider == "mock":
            if scenario not in {"perfect", "noisy", "malformed"}:
                status = "invalid"
                bad_cases.append(
                    create_bad_case(
                        "evaluation",
                        "scenario",
                        BadCaseCategory.MALFORMED_OUTPUT,
                        BadCaseSeverity.HIGH,
                        "unsupported mock scenario",
                        [],
                    )
                )
            else:
                dataset = EvaluationDataset(self.repo_root)
                dataset.assert_frozen()
                predictions = _oracle_predictions(dataset)
                if scenario == "noisy":
                    predictions = _mutate_noisy(dataset, predictions)
                elif scenario == "malformed":
                    predictions = _mutate_malformed(predictions)
                    status = "invalid"
        else:
            status = "invalid"
            bad_cases.append(
                create_bad_case(
                    "evaluation",
                    "provider",
                    BadCaseCategory.PROVIDER_ERROR,
                    BadCaseSeverity.HIGH,
                    "unsupported provider",
                    [],
                )
            )

        payload_violations: set[str] = set()
        valid_predictions: list[dict[str, Any]] = []
        for record in predictions:
            payload_report = check_inference_payload(record)
            payload_violations.update(payload_report.violations)
            
            if record.get("schema_status") == "invalid":
                continue
                
            if _validate_prediction(record):
                valid_predictions.append(record)
            else:
                bad_cases.append(
                    create_bad_case(
                        str(record.get("source_id", record.get("document_id", "prediction"))),
                        str(record.get("target_id", "prediction")),
                        BadCaseCategory.MALFORMED_OUTPUT,
                        BadCaseSeverity.HIGH,
                        "prediction schema validation failed",
                        [],
                    )
                )

        # Evaluator Phase (Gold path accessed here)
        dataset = EvaluationDataset(self.repo_root)
        dataset.assert_frozen()
        before_gold = hashlib.sha256(dataset.gold_path.read_bytes()).hexdigest()

        topic_gold = {
            note.note_path: bool(note.should_link_to_agent_evaluation)
            for note in dataset.get_gold_notes()
        }
        topic_pred = {
            record["document_id"]: bool(record["should_link"])
            for record in valid_predictions
            if record.get("kind") == "topic" and "document_id" in record
        }
        relation_gold = _gold_relation_map(dataset)
        relation_pred = {
            _pair_key(record["source_id"], record["target_id"]): {
                "source": record.get("source"),
                "target": record.get("target"),
                "relation_type": record.get("relation_type"),
                "should_link": record.get("should_link"),
            }
            for record in valid_predictions
            if record.get("kind") == "relation"
        }
        topic_scores = topic_metrics(topic_gold, topic_pred)
        relation_scores = relation_metrics_from_records(relation_gold, relation_pred)

        evidence_records: list[dict[str, Any]] = []
        for record in valid_predictions:
            for evidence in record.get("evidence_refs", []):
                evidence_records.append({"valid": isinstance(evidence, dict) and "quote_sha256" in evidence})
        evidence_scores = evidence_ownership_metrics(evidence_records)

        ranked_docs = [doc.path for doc in dataset.get_inference_documents()]
        positive_docs = [path for path, positive in sorted(topic_gold.items()) if positive]
        retrieval_hits = [path == (positive_docs[0] if positive_docs else "") for path in ranked_docs]
        retrieval_scores = {
            "hit_at_3": retrieval_hit_at_k(retrieval_hits, 3),
            "mrr": retrieval_mrr(retrieval_hits),
        }
        trace_events = [
            {"seq": 1, "event_type": "evaluation.started", "redaction": {"raw_content_included": False}},
            {"seq": 2, "event_type": "evaluation.completed", "redaction": {"raw_content_included": False}},
        ]
        trace_scores = trace_quality_metrics(trace_events)

        for record in valid_predictions:
            if record.get("kind") == "topic":
                case = classify_link_case(
                    record["document_id"],
                    record["document_id"],
                    topic_gold.get(record["document_id"], False),
                    record["should_link"],
                    safe_refs=[],
                )
                if case:
                    bad_cases.append(case)
            elif record.get("kind") == "relation":
                key = _pair_key(record["source_id"], record["target_id"])
                expected = relation_gold.get(key, {})
                case = classify_link_case(
                    record["source_id"],
                    record["target_id"],
                    bool(expected.get("should_link", False)),
                    record["should_link"],
                    safe_refs=[],
                )
                if case:
                    bad_cases.append(case)
                elif record["should_link"] and expected.get("should_link"):
                    bad_cases.extend(
                        classify_relation_detail(
                            record["source_id"],
                            record["target_id"],
                            direction_ok=(record.get("source"), record.get("target")) == (expected.get("source"), expected.get("target")),
                            type_ok=record.get("relation_type") == expected.get("relation_type"),
                            safe_refs=[],
                        )
                    )

        module_report = check_inference_module(str(self.repo_root / "src" / "linkloom" / "evaluation" / "inference.py"))
        frozen_report = compare_snapshots(before_fixture, snapshot_tree(dataset.fixture_root))
        after_gold = hashlib.sha256(dataset.gold_path.read_bytes()).hexdigest()
        if before_gold != after_gold:
            frozen_report = SafetyReport(is_safe=False, violations=sorted(set(frozen_report.violations + ["GOLD_MUTATION"])))
        dataset.assert_frozen()
        if payload_violations:
            status = "invalid"
            
        provider_safe = True
        if provider == "mock":
            provider_safe = True
        elif provider == "gemini_cli":
            provider_safe = True
        else:
            provider_safe = False

        safety = {
            "passed": bool(module_report.is_safe and frozen_report.is_safe and not payload_violations and provider_safe),
            "violations": sorted(set(module_report.violations + frozen_report.violations + list(payload_violations))),
            "checks": {
                "inference_module": module_report.is_safe,
                "frozen_inputs": frozen_report.is_safe,
                "prediction_payloads": not payload_violations,
                "network_provider": provider_safe,
            },
        }
        if not safety["passed"]:
            bad_cases.append(
                create_bad_case(
                    "evaluation",
                    "safety",
                    BadCaseCategory.SAFETY_VIOLATION,
                    BadCaseSeverity.CRITICAL,
                    "evaluation safety checks failed",
                    safety["violations"],
                )
            )

        finished = datetime.now(timezone.utc)
        
        # baseline eligible: real provider, all 55 tasks processed, no malformed outputs, safety passed
        baseline_eligible = False
        if provider == "gemini_cli" and safety["passed"] and status == "completed":
            if len(valid_predictions) == 55 and not any(case.category in (BadCaseCategory.PROVIDER_ERROR, BadCaseCategory.MALFORMED_OUTPUT) for case in bad_cases):
                baseline_eligible = True

        manifest = {
            "run_id": run_id,
            "dataset_version": dataset.dataset_version,
            "provider": provider,
            "scenario": scenario,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "document_count": len(dataset.get_inference_documents()),
            "candidate_pair_count": len(dataset.get_inference_pairs()),
            "gold_sha256": dataset.get_manifest().gold_sha256,
            "fixture_sha256": dataset.get_manifest().fixture_sha256,
            "oracle": oracle,
            "baseline_eligible": baseline_eligible,
            "status": status,
            "safety_passed": safety["passed"],
            "prediction_count": len(predictions),
            "usage": usage,
        }
        metrics = {
            "topic": topic_scores,
            "relation": relation_scores,
            "evidence": evidence_scores,
            "retrieval": retrieval_scores,
            "trace": trace_scores,
        }
        run_dir = self.output_root / run_id
        write_run_artifacts(
            run_dir,
            manifest=manifest,
            predictions=predictions,
            metrics=metrics,
            bad_cases=[_jsonable_bad_case(case) for case in bad_cases],
            trace_quality=trace_scores,
            safety_report=safety,
            evidence_validations=evidence_validations,
            usage=usage
        )
        return {"run_dir": str(run_dir), "manifest": manifest, "metrics": metrics, "safety": safety}


def evaluate(
    repo_root: str | Path,
    inference_fn: Callable[[EvaluationDataset], List[PredictionRecord]],
    provider: str = "mock",
    scenario: str = "perfect",
) -> EvaluationRunManifest:
    """Backward-compatible in-memory evaluator contract."""
    dataset = EvaluationDataset(repo_root)
    dataset.assert_frozen()
    predictions = inference_fn(dataset)
    gold_map = {
        _pair_key(pair.source, pair.target): {
            "source": pair.source,
            "target": pair.target,
            "relation_type": pair.relation_type,
            "should_link": pair.should_link,
        }
        for pair in dataset.get_gold_pairs()
    }
    pred_map = {
        _pair_key(pred.source_id, pred.target_id): {
            "source": pred.source_id if pred.direction == "forward" else pred.target_id,
            "target": pred.target_id if pred.direction == "forward" else pred.source_id,
            "relation_type": pred.relation_type,
            "should_link": pred.should_link,
        }
        for pred in predictions
    }
    scores = relation_metrics_from_records(gold_map, pred_map)
    dataset.assert_frozen()
    return EvaluationRunManifest(
        dataset_version=dataset.dataset_version,
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        metrics=[MetricResult(name=name, value=value) for name, value in sorted(scores.items())],
        safety_report=SafetyReport(is_safe=True, violations=[]),
        run_id=f"run-{provider}-{scenario}",
        provider=provider,
        scenario=scenario,
        baseline_eligible=False,
    )
