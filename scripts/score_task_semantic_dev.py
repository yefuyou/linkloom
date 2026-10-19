"""Offline scorer for the public M3-A task-semantic Dev predictions.

The scorer reads three independent JSON values: public inputs, author-visible
Gold, and a prediction artifact written before Gold is opened.  It never
imports the proposer or calls a Provider.  A ``REAL_PROVIDER`` artifact is
eligible for business metrics only for cases whose result was actually
executed.  ``NOT_RUN`` and fake/scripted artifacts remain explicitly outside
the business score.

The optional :func:`score_case` entry point can score a fixed candidate for a
contract diagnostic.  Callers must use ``execution_kind=\"SCRIPTED_CONTRACT_ONLY\"``
for that mode; its metrics are returned under ``diagnostic_metrics`` and are
never returned as ``business_metrics``.

Evidence validity and semantic correctness are intentionally separate:
``quote_authenticity`` measures the validation evidence readback, while
``gold_source_coverage`` measures whether the author-visible required source
set was cited.  Neither is silently promoted to ``SEMANTIC_ACCEPTED``.
The quote metric is explicitly a provided-validator attestation; this script
does not independently re-read source bytes.
"""

from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


SCHEMA = "task-semantic-score/v1"
PREDICTION_SCHEMA = "task-semantic-predictions/v1"
REAL_PROVIDER = "REAL_PROVIDER"
SCRIPTED_CONTRACT_ONLY = "SCRIPTED_CONTRACT_ONLY"
SUPPORTED_EXECUTION_KINDS = {REAL_PROVIDER, SCRIPTED_CONTRACT_ONLY}
VERDICTS = ("SUPPORTED", "REFUTED", "UNKNOWN", "CONFLICT")
SYMMETRIC_PREDICATES = {"SAME_TASK", "RELATED_TO", "PROJECT_ALIAS"}

# This is an author-visible, predeclared date diagnostic.  It is deliberately
# separate from Gold: date arithmetic is conditional on a model premise and
# never establishes semantic acceptance or publication authority.
DATE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "revision": {"status": "COMPUTABLE", "delta_calendar_days": 4},
    "conflict": {"status": "BLOCKED", "delta_calendar_days": None},
    "missing-year": {"status": "NOT_COMPARABLE", "delta_calendar_days": None},
}


def _load_json(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


def _metric(numerator: int, denominator: int, *, reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }
    if denominator == 0:
        result["status"] = "NOT_EVALUATED"
        result["reason"] = reason or "NO_ELIGIBLE_ITEMS"
    else:
        result["status"] = "SCORED"
    return result


def _not_evaluated(reason: str) -> dict[str, Any]:
    return {"status": "NOT_EVALUATED", "value": None, "reason": reason}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _endpoint_label(endpoint: Any, reference_map: Mapping[str, str]) -> str:
    """Resolve a typed endpoint dict to its public fixture handle when possible.

    Production proposals contain typed ``ObservationRef``/``ProjectRef``/
    ``ChangeRef`` dictionaries rather than public handles.  The scorer may use
    the public fixture helper only to bind those already-authorized endpoint
    values back to an input handle; it never loads Gold or enters the proposer.
    String handles are accepted for independent scripted contract tests.
    """

    if isinstance(endpoint, str):
        return endpoint
    if not isinstance(endpoint, Mapping):
        return "@invalid:" + hashlib.sha256(repr(endpoint).encode("utf-8")).hexdigest()[:16]
    if isinstance(endpoint.get("handle"), str):
        return endpoint["handle"]
    fingerprint = _stable_json(dict(endpoint))
    if fingerprint in reference_map:
        return reference_map[fingerprint]
    # A fabricated or stale endpoint must remain a distinct, scoreable key.
    return "@unknown:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def _proposal_id(proposal: Mapping[str, Any]) -> str:
    """Rebuild SemanticProposal.proposal_id for result.to_dict() rows.

    ``SemanticProposal.to_dict`` intentionally omits ``proposal_id``.  Its
    identity is the canonical hash of the serialized proposal without usage.
    Test-only candidates may provide an explicit id, which is retained.
    """

    explicit = proposal.get("proposal_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    payload = dict(proposal)
    payload.pop("usage", None)
    payload.pop("proposal_id", None)
    payload.pop("_score_key", None)
    return "semantic_proposal_v1_" + hashlib.sha256(
        _stable_json(payload).encode("utf-8")
    ).hexdigest()


def _reference_map(case: Mapping[str, Any]) -> dict[str, str]:
    """Build typed-reference fingerprints from public input only.

    The fixture helper is intentionally imported lazily and only from this
    offline scorer.  If a caller provides simple string handles, or runs a
    minimal synthetic case without the helper, scoring still works.
    """

    try:
        from tests.task_semantic_support import context_for

        context = context_for(dict(case))
    except Exception:
        return {}
    result: dict[str, str] = {}
    for handle, reference in context.endpoints:
        result[_stable_json(reference.to_dict())] = handle
    return result


def _relation_key(predicate: Any, subject: Any, object_: Any,
                  reference_map: Mapping[str, str]) -> tuple[str, str, str]:
    predicate = str(predicate)
    left = _endpoint_label(subject, reference_map)
    right = _endpoint_label(object_, reference_map)
    if predicate in SYMMETRIC_PREDICATES:
        left, right = sorted((left, right))
    return predicate, left, right


def _gold_relations(gold: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = gold.get("relations", [])
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _proposal_list(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = row.get("result")
    if not isinstance(result, Mapping):
        return []
    proposals = result.get("proposals")
    if not isinstance(proposals, list):
        return []
    return [dict(proposal) for proposal in proposals if isinstance(proposal, Mapping)]


def _normalize_row(prediction: Any) -> dict[str, Any]:
    if not isinstance(prediction, Mapping):
        return {"result": None, "validation": [], "comparisons": []}
    # score_case is useful with a bare SemanticResult.to_dict() as well as the
    # predictions/v1 case wrapper.
    if "result" in prediction:
        return dict(prediction)
    return {"result": dict(prediction), "validation": [], "comparisons": []}


def _citation_items(proposal: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for field in ("supporting_evidence", "counter_evidence"):
        values = proposal.get(field, [])
        if isinstance(values, list):
            items.extend(item for item in values if isinstance(item, Mapping))
    return items


def _citation_signature(citation: Mapping[str, Any]) -> tuple[Any, ...]:
    span = citation.get("span") if isinstance(citation.get("span"), Mapping) else citation
    span = dict(span) if isinstance(span, Mapping) else {}
    source = span.get("artifact_id")
    if source is None:
        source_ref = span.get("source_ref") or span.get("evidence_ref")
        if isinstance(source_ref, str):
            source = source_ref.rsplit("/", 1)[-1]
    quote = citation.get("quote", span.get("quote"))
    start = citation.get("char_start", span.get("char_start"))
    end = citation.get("char_end", span.get("char_end"))
    # Model citations carry the full authorized span plus a quote substring;
    # host validation carries the resolved exact offsets.  Reconstruct the
    # unique codepoint offset for comparison without treating it as trust.
    if "char_start" not in citation and isinstance(start, int):
        full_quote = span.get("quote")
        if isinstance(full_quote, str) and isinstance(quote, str):
            first = full_quote.find(quote)
            if first >= 0 and full_quote.find(quote, first + 1) < 0:
                start = start + first
                end = start + len(quote)
    return source, start, end, quote


def _validation_by_proposal(row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = row.get("validation", [])
    if not isinstance(values, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in values:
        if isinstance(item, Mapping):
            proposal_id = item.get("proposal_id")
            if isinstance(proposal_id, str) and proposal_id:
                result[proposal_id] = item
    return result


def _quote_counts(proposals: Iterable[Mapping[str, Any]], row: Mapping[str, Any]) -> tuple[int, int]:
    """Return (valid, total) quote counts from output and host validation.

    The validation result is a readback certificate for references only.  A
    valid quote still does not make its relation semantically correct.
    """

    validation = _validation_by_proposal(row)
    valid = total = 0
    for proposal in proposals:
        citations = _citation_items(proposal)
        total += len(citations)
        report = validation.get(_proposal_id(proposal))
        if not report:
            continue
        accepted = []
        for field in ("supporting_evidence", "counter_evidence"):
            values = report.get(field, [])
            if isinstance(values, list):
                accepted.extend(item for item in values if isinstance(item, Mapping))
        accepted_counts = Counter(_citation_signature(item) for item in accepted)
        for citation in citations:
            signature = _citation_signature(citation)
            if accepted_counts[signature]:
                valid += 1
                accepted_counts[signature] -= 1
    return valid, total


def _citation_sources(proposal: Mapping[str, Any]) -> set[str]:
    sources: set[str] = set()
    for citation in _citation_items(proposal):
        span = citation.get("span") if isinstance(citation.get("span"), Mapping) else citation
        if not isinstance(span, Mapping):
            continue
        source = span.get("artifact_id")
        if source is None:
            source_ref = span.get("source_ref") or span.get("evidence_ref")
            if isinstance(source_ref, str):
                source = source_ref.rsplit("/", 1)[-1]
        if isinstance(source, str) and source:
            sources.add(source)
        span_id = citation.get("span_id")
        if isinstance(span_id, str):
            sources.add(span_id.split(":", 1)[0])
    return sources


def _proposal_verdict(proposal: Mapping[str, Any]) -> str | None:
    verdict = proposal.get("proposed_verdict")
    return verdict if isinstance(verdict, str) else None


def _relation_maps(case: Mapping[str, Any], gold: Mapping[str, Any],
                  proposals: list[dict[str, Any]]):
    references = _reference_map(case)
    expected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for relation in _gold_relations(gold):
        key = _relation_key(relation.get("predicate"), relation.get("subject"),
                            relation.get("object"), references)
        expected[key] = relation
    predicted: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for proposal in proposals:
        key = _relation_key(proposal.get("predicate"), proposal.get("subject"),
                            proposal.get("object"), references)
        proposal["_score_key"] = key
        predicted[key].append(proposal)
    return expected, predicted


def _date_metric(case_id: str, expected: Mapping[tuple[str, str, str], Mapping[str, Any]],
                 predicted: Mapping[tuple[str, str, str], list[dict[str, Any]]],
                 row: Mapping[str, Any]) -> dict[str, Any]:
    standard = DATE_EXPECTATIONS.get(case_id)
    if standard is None:
        return _not_evaluated("NO_PREDECLARED_DATE_STANDARD")
    revision_keys = [key for key in expected if key[0] == "REVISES_PLAN"]
    if not revision_keys:
        return _not_evaluated("NO_REVISION_RELATION")
    key = revision_keys[0]
    candidates = predicted.get(key, [])
    comparisons = row.get("comparisons", [])
    by_id = {
        item.get("proposal_id"): item
        for item in comparisons
        if isinstance(item, Mapping) and isinstance(item.get("proposal_id"), str)
    } if isinstance(comparisons, list) else {}
    correct = False
    if len(candidates) == 1:
        proposal = candidates[0]
        comparison = by_id.get(_proposal_id(proposal))
        if isinstance(comparison, Mapping):
            correct = (
                comparison.get("status") == standard["status"]
                and comparison.get("delta_calendar_days") == standard["delta_calendar_days"]
                and comparison.get("conditional_on_model_semantics") is True
                and comparison.get("semantic_status") == "NOT_EVALUATED"
                and comparison.get("publishable") is False
            )
    return _metric(int(correct), 1, reason="CONDITIONAL_DATE_RESULT_MISSING_OR_INVALID")


def _score_metrics(case: Mapping[str, Any], gold: Mapping[str, Any],
                   row: Mapping[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    expected, predicted = _relation_maps(case, gold, proposals)
    state_correct = 0
    state_by_expected: dict[str, dict[str, int]] = {
        verdict: {"correct": 0, "total": 0} for verdict in VERDICTS
    }
    missing_gold = 0
    duplicate_keys = 0
    unexpected = 0
    for key, relation in expected.items():
        candidates = predicted.get(key, [])
        if not candidates:
            missing_gold += 1
        if len(candidates) > 1:
            duplicate_keys += len(candidates) - 1
        expected_verdict = relation.get("verdict")
        if expected_verdict not in state_by_expected:
            continue
        state_by_expected[expected_verdict]["total"] += 1
        if len(candidates) == 1 and _proposal_verdict(candidates[0]) == expected_verdict:
            state_correct += 1
            state_by_expected[expected_verdict]["correct"] += 1
    for key, candidates in predicted.items():
        if key not in expected:
            unexpected += len(candidates)

    per_predicate: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for key, relation in expected.items():
        predicate = key[0]
        candidates = predicted.get(key, [])
        positives = sum(_proposal_verdict(item) == "SUPPORTED" for item in candidates)
        expected_positive = relation.get("verdict") == "SUPPORTED"
        per_predicate[predicate]["tp"] += int(expected_positive and positives > 0)
        per_predicate[predicate]["fn"] += int(expected_positive and positives == 0)
        per_predicate[predicate]["fp"] += max(0, positives - int(expected_positive and positives > 0))
    for key, candidates in predicted.items():
        if key in expected:
            continue
        for proposal in candidates:
            if _proposal_verdict(proposal) == "SUPPORTED":
                per_predicate[key[0]]["fp"] += 1

    relation_positive_precision = {
        predicate: _metric(values["tp"], values["tp"] + values["fp"])
        for predicate, values in sorted(per_predicate.items())
    }
    relation_positive_recall = {
        predicate: _metric(values["tp"], values["tp"] + values["fn"])
        for predicate, values in sorted(per_predicate.items())
    }
    relation_by_predicate = {
        predicate: {
            "precision": relation_positive_precision[predicate],
            "recall": relation_positive_recall[predicate],
            **values,
        }
        for predicate, values in sorted(per_predicate.items())
    }

    false_merge_denominator = 0
    false_merge_count = 0
    false_revision_denominator = 0
    false_revision_count = 0
    for key, candidates in predicted.items():
        for index, proposal in enumerate(candidates):
            if _proposal_verdict(proposal) != "SUPPORTED":
                continue
            relation = expected.get(key)
            if key[0] == "SAME_TASK":
                false_merge_denominator += 1
                if relation is None or relation.get("verdict") != "SUPPORTED" or index:
                    false_merge_count += 1
            if key[0] == "REVISES_PLAN":
                false_revision_denominator += 1
                if relation is None or relation.get("verdict") != "SUPPORTED" or index:
                    false_revision_count += 1

    quote_valid, quote_total = _quote_counts(proposals, row)
    source_covered = 0
    grounded = 0
    validation = _validation_by_proposal(row)
    for key, relation in expected.items():
        candidates = predicted.get(key, [])
        if len(candidates) != 1:
            continue
        proposal = candidates[0]
        required = {
            source for source in relation.get("required_sources", []) if isinstance(source, str)
        }
        covered = required.issubset(_citation_sources(proposal))
        source_covered += int(covered)
        proposal_quotes = _citation_items(proposal)
        report = validation.get(_proposal_id(proposal))
        accepted = []
        if isinstance(report, Mapping):
            for field in ("supporting_evidence", "counter_evidence"):
                values = report.get(field, [])
                if isinstance(values, list):
                    accepted.extend(item for item in values if isinstance(item, Mapping))
        accepted_set = Counter(_citation_signature(item) for item in accepted)
        all_quotes_valid = bool(proposal_quotes) and all(
            accepted_set[_citation_signature(item)] > 0 for item in proposal_quotes
        )
        if (
            covered
            and _proposal_verdict(proposal) == relation.get("verdict")
            and all_quotes_valid
            and isinstance(report,Mapping) and report.get('stage')=='EVIDENCE_VALID'
        ):
            grounded += 1

    state_accuracy = _metric(state_correct, len(expected))
    metrics: dict[str, Any] = {
        "scope": "EXECUTED_REAL_PROVIDER_CASES_ONLY",
        "relation_positive_precision": relation_positive_precision,
        "relation_positive_recall": relation_positive_recall,
        "relation_by_predicate": relation_by_predicate,
        "relation_state_accuracy": {
            **state_accuracy,
            "by_expected_state": {
                state: _metric(values["correct"], values["total"])
                for state, values in state_by_expected.items()
            },
        },
        # Alias makes the semantic-vs-evidence distinction explicit to report
        # consumers while retaining the familiar relation_state name.
        "semantic_label_accuracy": state_accuracy,
        "false_merge": {
            **_metric(false_merge_count, false_merge_denominator),
            "count": false_merge_count,
        },
        "false_revision": {
            **_metric(false_revision_count, false_revision_denominator),
            "count": false_revision_count,
        },
        "quote_authenticity": _metric(quote_valid, quote_total),
        "gold_source_coverage": _metric(source_covered, len(expected)),
        "evidence_groundedness": _metric(grounded, len(expected)),
        "conditional_date_correctness": _date_metric(
            str(case.get("case_id")), expected, predicted, row
        ),
        "counts": {
            "gold_relations": len(expected),
            "predicted_proposals": len(proposals),
            "missing_gold_relations": missing_gold,
            "duplicate_predictions": duplicate_keys,
            "unexpected_proposals": unexpected,
            "quote_valid": quote_valid,
            "quote_total": quote_total,
            "gold_source_covered": source_covered,
            "evidence_grounded": grounded,
            "state_correct": state_correct,
            "state_total": len(expected),
        },
        "evidence_basis": {
            "quote_authenticity": "PROVIDED_VALIDATOR_ATTESTATION_ONLY",
            "gold_source_coverage": "AUTHOR_VISIBLE_GOLD_REQUIRED_SOURCES",
            "semantic_labels": "PROPOSED_VERDICT_VS_GOLD",
            "semantic_status": "NOT_EVALUATED",
        },
    }
    return metrics


def score_case(case: Mapping[str, Any], gold: Mapping[str, Any], prediction: Any,
               *, execution_kind: str = SCRIPTED_CONTRACT_ONLY) -> dict[str, Any]:
    """Score one case with an explicit production-vs-diagnostic boundary.

    ``REAL_PROVIDER`` + an executed result produces business metrics.
    ``SCRIPTED_CONTRACT_ONLY`` computes the same math only for a fixed
    diagnostic and places it under ``diagnostic_metrics``.  ``NOT_RUN`` and a
    pre-provider ``BLOCKED`` result are reported without a business score.
    A real-provider ``OUTPUT_INVALID`` result is treated as an executed empty
    output, so every Gold relation remains a false negative instead of being
    silently dropped.
    """

    case_id = str(case.get("case_id", ""))
    row = _normalize_row(prediction)
    result = row.get("result")
    status = result.get("status") if isinstance(result, Mapping) else None
    provider_calls = (
        int(result.get("provider_calls", 0) or 0)
        if isinstance(result, Mapping)
        and type(result.get("provider_calls", 0) or 0) is int
        else 0
    )
    if execution_kind not in SUPPORTED_EXECUTION_KINDS:
        return {
            "schema": SCHEMA,
            "case_id": case_id,
            "status": "TEST_ONLY",
            "score_scope": execution_kind,
            "business_metrics": None,
            "diagnostic_metrics": None,
            "reason": "UNSUPPORTED_OR_FAKE_EXECUTION_KIND",
        }
    if status == "NOT_RUN" or status is None and prediction is None:
        return {
            "schema": SCHEMA,
            "case_id": case_id,
            "status": "NOT_EVALUATED",
            "score_scope": execution_kind,
            "business_metrics": None,
            "diagnostic_metrics": None,
            "reason": "NOT_RUN" if status == "NOT_RUN" else "MISSING_OUTPUT",
        }
    if status != "EXECUTED" and provider_calls <= 0:
        return {
            "schema": SCHEMA,
            "case_id": case_id,
            "status": "NOT_EVALUATED",
            "score_scope": execution_kind,
            "business_metrics": None,
            "diagnostic_metrics": None,
            "reason": "PROVIDER_NOT_CALLED",
        }
    if execution_kind == REAL_PROVIDER and (
        status == "EXECUTED" and provider_calls <= 0
    ):
        return {
            "schema": SCHEMA,
            "case_id": case_id,
            "status": "NOT_EVALUATED",
            "score_scope": execution_kind,
            "business_metrics": None,
            "diagnostic_metrics": None,
            "reason": "EXECUTED_WITHOUT_PROVIDER_CALL_RECEIPT",
        }

    # OUTPUT_INVALID is an empty model answer for scoring purposes.  Do not
    # let a malformed artifact smuggle an attached proposal into the metrics.
    proposals = _proposal_list(row) if status == "EXECUTED" else []
    # A malformed output is an executed empty response when the Provider was
    # reached.  This preserves every Gold relation in the recall denominator.
    invalid_output = status != "EXECUTED"
    metrics = _score_metrics(case, gold, row, proposals)
    if execution_kind == REAL_PROVIDER:
        return {
            "schema": SCHEMA,
            "case_id": case_id,
            "status": "SCORED",
            "score_scope": REAL_PROVIDER,
            "business_metrics": metrics,
            "diagnostic_metrics": None,
            "invalid_output": invalid_output,
            "reason": "OUTPUT_INVALID_SCORED_AS_MISSES" if invalid_output else None,
        }
    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "status": "SCRIPTED_CONTRACT_ONLY",
        "score_scope": SCRIPTED_CONTRACT_ONLY,
        "business_metrics": None,
        "diagnostic_metrics": metrics,
        "invalid_output": invalid_output,
        "reason": "FIXED_CANDIDATE_DIAGNOSTIC_ONLY",
    }


def _sum_metric_rows(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, Any]:
    numerator = denominator = 0
    for row in rows:
        metric = row.get(key)
        if isinstance(metric, Mapping):
            numerator += int(metric.get("numerator", 0) or 0)
            denominator += int(metric.get("denominator", 0) or 0)
    return _metric(numerator, denominator)


def _aggregate_metrics(metrics_rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not metrics_rows:
        return None
    predicates: set[str] = set()
    for row in metrics_rows:
        predicates.update((row.get("relation_positive_precision") or {}).keys())
        predicates.update((row.get("relation_positive_recall") or {}).keys())
    precision: dict[str, Any] = {}
    recall: dict[str, Any] = {}
    by_predicate: dict[str, Any] = {}
    for predicate in sorted(predicates):
        p_rows = [row["relation_positive_precision"][predicate]
                  for row in metrics_rows if predicate in row.get("relation_positive_precision", {})]
        r_rows = [row["relation_positive_recall"][predicate]
                  for row in metrics_rows if predicate in row.get("relation_positive_recall", {})]
        p = _metric(sum(int(item.get("numerator", 0) or 0) for item in p_rows),
                    sum(int(item.get("denominator", 0) or 0) for item in p_rows))
        r = _metric(sum(int(item.get("numerator", 0) or 0) for item in r_rows),
                    sum(int(item.get("denominator", 0) or 0) for item in r_rows))
        precision[predicate], recall[predicate] = p, r
        by_predicate[predicate] = {"precision": p, "recall": r,
            "tp": p["numerator"], "fp": p["denominator"]-p["numerator"],
            "fn": r["denominator"]-r["numerator"]}

    state = _sum_metric_rows(metrics_rows, "relation_state_accuracy")
    state["by_expected_state"] = {}
    for verdict in VERDICTS:
        items=[row["relation_state_accuracy"]["by_expected_state"][verdict] for row in metrics_rows]
        state["by_expected_state"][verdict]=_metric(
            sum(item["numerator"] for item in items),sum(item["denominator"] for item in items))
    false_merge = _sum_metric_rows(metrics_rows, "false_merge")
    false_revision = _sum_metric_rows(metrics_rows, "false_revision")
    aggregate: dict[str, Any] = {
        "scope": "EXECUTED_REAL_PROVIDER_CASES_ONLY",
        "relation_positive_precision": precision,
        "relation_positive_recall": recall,
        "relation_by_predicate": by_predicate,
        "relation_state_accuracy": state,
        "semantic_label_accuracy": state,
        "false_merge": false_merge,
        "false_revision": false_revision,
        "quote_authenticity": _sum_metric_rows(metrics_rows, "quote_authenticity"),
        "gold_source_coverage": _sum_metric_rows(metrics_rows, "gold_source_coverage"),
        "evidence_groundedness": _sum_metric_rows(metrics_rows, "evidence_groundedness"),
        "conditional_date_correctness": _sum_metric_rows(metrics_rows, "conditional_date_correctness"),
    }
    counts: Counter[str] = Counter()
    for row in metrics_rows:
        raw = row.get("counts", {})
        if isinstance(raw, Mapping):
            counts.update({key: int(value or 0) for key, value in raw.items()})
    aggregate["counts"] = dict(counts)
    return aggregate


def _provider_receipts_are_attested(predictions: Mapping[str, Any],
                                    *, required_returned: int) -> bool:
    """Require local durable call evidence before granting a real score.

    This is an artifact consistency check, not remote or cryptographic
    attestation.  An ``ATTEMPTED`` row alone cannot substantiate an EXECUTED
    case.
    """

    calls = predictions.get("provider_calls")
    receipts = predictions.get("provider_receipts")
    if type(calls) is not int or calls <= 0 or not isinstance(receipts, list):
        return False
    if len(receipts) != calls or any(not isinstance(receipt,Mapping) for receipt in receipts):
        return False
    if sum(receipt.get("status") == "RETURNED" for receipt in receipts) < required_returned:
        return False
    return all(
        isinstance(receipt, Mapping)
        and receipt.get("status") in {"ATTEMPTED", "RETURNED", "FAILED"}
        for receipt in receipts
    )


def score_predictions(inputs: str | Path | Mapping[str, Any],
                      gold: str | Path | Mapping[str, Any],
                      predictions: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Score a prediction artifact while making partial execution explicit."""

    input_data = _load_json(inputs)
    gold_data = _load_json(gold)
    prediction_data = _load_json(predictions)
    execution_kind = prediction_data.get("execution_kind")
    if execution_kind not in SUPPORTED_EXECUTION_KINDS:
        execution_kind = str(execution_kind or "UNKNOWN")
    input_cases = input_data.get("cases", [])
    gold_cases = {row.get("case_id"): row for row in gold_data.get("cases", [])
                  if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)}
    raw_rows=prediction_data.get("cases")
    valid_rows=isinstance(raw_rows,list) and all(isinstance(row,Mapping)
        and isinstance(row.get("case_id"),str) for row in raw_rows)
    ids=Counter(row["case_id"] for row in raw_rows) if valid_rows else Counter()
    input_ids={case["case_id"] for case in input_cases}
    duplicates=sorted(key for key,count in ids.items() if count>1)
    unexpected=sorted(set(ids)-input_ids)
    invalid_reason=('INVALID_PREDICTION_SCHEMA' if prediction_data.get('schema')!=PREDICTION_SCHEMA
        else 'MALFORMED_CASE_ROWS' if not valid_rows else 'DUPLICATE_CASE_IDS' if duplicates
        else 'UNEXPECTED_CASE_IDS' if unexpected else None)
    if invalid_reason:
        return dict(schema=SCHEMA,status='NOT_EVALUATED',execution_kind=execution_kind,
            business_metrics=None,diagnostic_metrics=None,reason=invalid_reason,case_scores=[],
            coverage=dict(total_cases=len(input_cases),duplicate_case_ids=duplicates,
                unexpected_case_ids=unexpected,missing_case_rows=len(input_ids-set(ids))))
    prediction_cases = {row['case_id']:row for row in raw_rows}
    executed_case_count = sum(row['result'].get('provider_calls',0)
        for row in prediction_cases.values() if isinstance(row.get('result'),Mapping)
        and row['result'].get('status')=='EXECUTED' and type(row['result'].get('provider_calls',0)) is int)
    receipt_attested = (
        execution_kind != REAL_PROVIDER
        or (_provider_receipts_are_attested(prediction_data, required_returned=executed_case_count)
            and all(isinstance(row.get('result'),Mapping)
                and type(row['result'].get('provider_calls',0)) is int
                and row['result'].get('provider_calls',0)>=0 for row in raw_rows)
            and sum(row['result'].get('provider_calls',0) for row in raw_rows)==prediction_data.get('provider_calls'))
    )
    case_scores = []
    for case in input_cases:
        case_id = case.get("case_id") if isinstance(case, Mapping) else None
        if not isinstance(case_id, str) or case_id not in gold_cases:
            continue
        row = prediction_cases.get(case_id)
        has_calls=(isinstance(row,Mapping) and isinstance(row.get('result'),Mapping)
            and type(row['result'].get('provider_calls',0)) is int and row['result'].get('provider_calls',0)>0)
        if execution_kind == REAL_PROVIDER and not receipt_attested and has_calls:
            score = {
                "schema": SCHEMA,
                "case_id": case_id,
                "status": "NOT_EVALUATED",
                "score_scope": REAL_PROVIDER,
                "business_metrics": None,
                "diagnostic_metrics": None,
                "reason": "EXECUTION_RECEIPTS_MISSING_OR_INVALID",
            }
        else:
            score = score_case(case, gold_cases[case_id], row,
                               execution_kind=execution_kind)
        case_scores.append(score)

    business_rows = [score["business_metrics"] for score in case_scores
                     if score.get("business_metrics") is not None]
    diagnostic_rows = [score["diagnostic_metrics"] for score in case_scores
                       if score.get("diagnostic_metrics") is not None]
    coverage = {
        "total_cases": len(input_cases),
        "executed_real_cases": sum(
            score.get("status") == "SCORED" and score.get("score_scope") == REAL_PROVIDER
            for score in case_scores
        ),
        "not_run_cases": sum(
            score.get("status") == "NOT_EVALUATED"
            and score.get("reason") in {"NOT_RUN", "PROVIDER_NOT_CALLED", "MISSING_OUTPUT"}
            for score in case_scores
        ),
        "invalid_output_cases": sum(bool(score.get("invalid_output")) for score in case_scores),
        "scripted_diagnostic_cases": sum(score.get("status") == "SCRIPTED_CONTRACT_ONLY"
                                          for score in case_scores),
        "missing_case_rows": len(set(gold_cases) - set(prediction_cases)),
        "execution_receipts_attested": receipt_attested,
        "duplicate_case_ids": duplicates,
        "unexpected_case_ids": unexpected,
    }
    if execution_kind == REAL_PROVIDER:
        if coverage["executed_real_cases"] == 0:
            status = "NOT_EVALUATED"
        elif coverage["executed_real_cases"] < coverage["total_cases"]:
            status = "PARTIAL_EXECUTION"
        else:
            status = "SCORED"
        return {
            "schema": SCHEMA,
            "status": status,
            "execution_kind": REAL_PROVIDER,
            "business_metrics": _aggregate_metrics(business_rows),
            "diagnostic_metrics": None,
            "coverage": coverage,
            "case_scores": case_scores,
        }
    if execution_kind == SCRIPTED_CONTRACT_ONLY:
        return {
            "schema": SCHEMA,
            "status": "TEST_ONLY",
            "execution_kind": SCRIPTED_CONTRACT_ONLY,
            "business_metrics": None,
            "diagnostic_metrics": _aggregate_metrics(diagnostic_rows),
            "coverage": coverage,
            "case_scores": case_scores,
        }
    return {
        "schema": SCHEMA,
        "status": "NOT_EVALUATED",
        "execution_kind": execution_kind,
        "business_metrics": None,
        "diagnostic_metrics": None,
        "coverage": coverage,
        "case_scores": case_scores,
        "reason": "REAL_PROVIDER_EXECUTION_KIND_REQUIRED",
    }


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = score_predictions(args.inputs, args.gold, args.predictions)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:
            stream.write(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
