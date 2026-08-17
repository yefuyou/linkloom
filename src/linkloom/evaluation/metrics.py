"""Pure deterministic evaluation metrics with explicit zero-denominator rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Dict, List


def _round(value: float) -> float:
    return round(float(value), 4)


def safe_divide(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else _round(numerator / denominator)


def binary_metrics(tp: int, fp: int, tn: int, fn: int) -> Dict[str, float]:
    total = tp + fp + tn + fn
    return {
        "accuracy": safe_divide(tp + tn, total),
        "precision": safe_divide(tp, tp + fp),
        "recall": safe_divide(tp, tp + fn),
        "f1": safe_divide(2 * tp, 2 * tp + fp + fn),
        "fpr": safe_divide(fp, fp + tn),
        "fnr": safe_divide(fn, fn + tp),
    }


def topic_metrics(gold: Mapping[str, bool], predicted: Mapping[str, bool]) -> Dict[str, float]:
    tp = fp = tn = fn = 0
    for path in sorted(set(gold) | set(predicted)):
        actual = bool(gold.get(path, False))
        guess = bool(predicted.get(path, False))
        if actual and guess:
            tp += 1
        elif not actual and guess:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    return binary_metrics(tp, fp, tn, fn)


def relation_metrics(
    link_tp: int,
    link_fp: int,
    link_fn: int,
    direction_correct: int,
    direction_total: int,
    type_correct: int,
    type_total: int,
) -> Dict[str, float]:
    precision = safe_divide(link_tp, link_tp + link_fp)
    recall = safe_divide(link_tp, link_tp + link_fn)
    f1 = safe_divide(2 * precision * recall, precision + recall)
    return {
        "link_precision": precision,
        "link_recall": recall,
        "link_f1": f1,
        "direction_accuracy": safe_divide(direction_correct, direction_total),
        "type_accuracy": safe_divide(type_correct, type_total),
    }


def relation_metrics_from_records(
    gold: Mapping[tuple[str, str], Mapping[str, Any]],
    predicted: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Dict[str, float]:
    link_tp = link_fp = link_fn = direction_correct = type_correct = 0
    direction_total = type_total = 0
    keys = sorted(set(gold) | set(predicted))
    for key in keys:
        expected = gold.get(key, {})
        guess = predicted.get(key, {})
        gold_link = bool(expected.get("should_link", False))
        pred_link = bool(guess.get("should_link", False))
        if gold_link and pred_link:
            link_tp += 1
            direction_total += 1
            type_total += 1
            direction_correct += int((guess.get("source"), guess.get("target")) == (expected.get("source"), expected.get("target")))
            type_correct += int(guess.get("relation_type") == expected.get("relation_type"))
        elif not gold_link and pred_link:
            link_fp += 1
        elif gold_link and not pred_link:
            link_fn += 1
    return relation_metrics(link_tp, link_fp, link_fn, direction_correct, direction_total, type_correct, type_total)


def evidence_metrics(valid_evidence: int, total_evidence: int) -> float:
    return safe_divide(valid_evidence, total_evidence)


def evidence_ownership_metrics(records: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    records = list(records)
    valid = sum(1 for record in records if record.get("valid") is True)
    return {"valid_rate": evidence_metrics(valid, len(records)), "valid": float(valid), "total": float(len(records))}


def retrieval_hit_at_k(hits: Sequence[bool], k: int) -> float:
    if k <= 0:
        return 0.0
    return 1.0 if any(bool(hit) for hit in hits[:k]) else 0.0


def retrieval_mrr(hits: Sequence[bool]) -> float:
    for index, hit in enumerate(hits):
        if hit:
            return _round(1.0 / (index + 1))
    return 0.0


def trace_quality(sequence_count: int, redaction_count: int) -> Dict[str, int]:
    return {"sequence_count": sequence_count, "redaction_count": redaction_count}


def trace_quality_metrics(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    seqs = [event.get("seq") for event in events]
    contiguous = seqs == list(range(1, len(seqs) + 1))
    redactions = sum(1 for event in events if event.get("redaction"))
    raw_content = sum(
        1
        for event in events
        if isinstance(event.get("redaction"), Mapping) and event["redaction"].get("raw_content_included") is True
    )
    return {
        "sequence_count": len(events),
        "redaction_count": redactions,
        "contiguous": contiguous,
        "raw_content_events": raw_content,
    }
