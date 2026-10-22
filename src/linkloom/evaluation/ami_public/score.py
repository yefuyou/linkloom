"""Post-seal official-link scoring, including exact context retention and failures."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from linkloom.evaluation.metrics import retrieval_hit_at_k
from linkloom.indexing.bm25 import tokenize_for_index
from .common import (VERSION, passage_logical_path, passage_source_ref, read_json, read_rows, sha, unique_rows,
                     verify_files, write_json)


def official_link_metrics(ranked: list[str], required: set[str]) -> dict | None:
    """Known-human-link target recall, not exhaustive semantic relevance recall.

    The shared hit helper is reused. Historical retrieval_case_metrics rounds
    individual cases and lacks @10; retain full fractions here for an exact
    same-scorer comparison with the frozen reference (no historical API edits).
    """
    if not required:
        return None
    flags = [item in required for item in ranked[:10]]
    metrics = {f"recall_at_{k}": len(set(ranked[:k]) & required) / len(required) for k in (1, 5, 10)}
    metrics.update({f"hit_at_{k}": retrieval_hit_at_k(flags, k) for k in (1, 5, 10)})
    first = next((i + 1 for i, hit in enumerate(flags) if hit), None)
    metrics["mrr_at_10"] = 1 / first if first else 0.0
    return metrics


def aggregate(rows: list[dict]) -> dict:
    eligible = [r for r in rows if r["metrics"] is not None]
    count = len(eligible)
    unexecuted = [r for r in rows if r.get("required_count", 0) > 0 and r["metrics"] is None]
    keys = ("recall_at_1", "recall_at_5", "recall_at_10", "hit_at_1", "hit_at_5", "hit_at_10", "mrr_at_10")
    required = sum(r["required_count"] for r in eligible)
    return {"scheduled": len(rows), "eligible": count + len(unexecuted),
        "not_evaluable": len(rows) - count - len(unexecuted), "not_executed_eligible": len(unexecuted),
        "completed_eligible": sum(r.get("execution_status", "SUCCESS") == "SUCCESS" for r in eligible),
        "official_link_metrics": {key: sum(r["metrics"][key] for r in eligible) / count if count else None for key in keys},
        "metric_denominator": count,
        "required_evidence_coverage_macro": sum(r["context_count"] / r["required_count"] for r in eligible) / count if count else None,
        "required_evidence_coverage_micro": sum(r["context_count"] for r in eligible) / required if required else None,
        "required_target_pairs": required,
        "context_retained_target_pairs": sum(r["context_count"] for r in eligible),
        "retrieved_at_10_target_pairs": sum(r["retrieved_count"] for r in eligible),
        "selection_retained_target_pairs": sum(r["selection_count"] for r in eligible),
        "chunk_lost_target_pairs": sum(len(r["chunk_lost"]) for r in eligible),
        "selection_lost_target_pairs": sum(len(r["selection_lost"]) for r in eligible),
        "assembly_lost_target_pairs": sum(len(r["assembly_lost"]) for r in eligible),
        "truncated_target_pairs": sum(len(r["truncated"]) for r in eligible),
        "encoder_truncated_target_pairs": sum(len(r.get("encoder_truncated", [])) for r in eligible),
        "selection_drop_reasons": dict(Counter(reason for r in eligible for reason in r.get("selection_drop_reasons", [])))}


def verify_seal(run: Path) -> dict:
    seal = read_json(run / "output_seal.json")
    if seal.get("sealed") is not True or any(
        seal.get(key + "_sha256") != sha((run / filename).read_bytes())
        for key, filename in (("predictions", "predictions.jsonl"), ("run_manifest", "run_manifest.json"))):
        raise ValueError("prediction seal mismatch; Gold remains unopened")
    return seal


def evaluate_query(query: dict, prediction: dict | None, passages: dict, acts: dict) -> dict:
    required = set(query["relevant_dialogue_act_ids"])
    prediction = prediction or {"execution_status": "NOT_STARTED", "ranked": [], "context": {}}
    hits = prediction["ranked"]
    if len(hits) > 10 or len({h["evidence_id"] for h in hits}) != len(hits):
        raise ValueError("invalid ranked candidate list")
    by_act = defaultdict(set)
    for p in passages.values():
        if p["meeting_id"] == query["meeting_id"]:
            by_act[p["utterance_id"]].add(p["piece_id"])
    prefix, ranked = set(), []
    for rank, hit in enumerate(hits, 1):
        p = passages[hit["resource_id"]]
        if hit["rank"] != rank or hit["evidence_id"] != p["piece_id"]:
            raise ValueError("rank or evidence identity mismatch")
        if p["meeting_id"] != query["meeting_id"] or any(hit["metadata"].get(k) != v for k, v in p.items()):
            raise ValueError("prediction source identity/text does not match hashed corpus")
        if (hit["source_ref"] != passage_source_ref(p) or hit["logical_path"] != passage_logical_path(p)
            or hit["metadata"].get("workspace_id") != p["meeting_id"]
            or hit["metadata"].get("content_hash") != p["canonical_sha256"]
            or hit["metadata"].get("quote_sha256") != p["text_sha256"]):
            raise ValueError("ranked source provenance mismatch")
        prefix.add(p["piece_id"])
        ranked.append(p["utterance_id"] if by_act[p["utterance_id"]].issubset(prefix) else "INCOMPLETE_DA")
    context = prediction.get("context", {})
    selected_ids = set((context.get("selection") or {}).get("selected_ids", []))
    selected_items = {i["item_id"]: i for i in context.get("selected", []) if i["source_type"] == "evidence"}
    rendered = context.get("rendered_text", "")
    if not selected_ids.issubset(prefix) or not set(selected_items).issubset(prefix):
        raise ValueError("context selected evidence not present in retrieval")
    if not set(selected_items).issubset(selected_ids):
        raise ValueError("rendered evidence contradicts selector selection")
    if prediction["execution_status"] == "SUCCESS" and (
        context.get("workspace_id") != query["meeting_id"] or context.get("query") != query["query_text"].strip()):
        raise ValueError("context query/workspace provenance mismatch")
    for pid, item in selected_items.items():
        if tuple(item["source_refs"]) != (passage_source_ref(passages[pid]),):
            raise ValueError("context source provenance mismatch")
    rendered_ids = {pid for pid, item in selected_items.items()
                    if passages[pid]["text"] in item["text"] and item["text"] in rendered}
    retrieved = {a for a in required if by_act[a] and by_act[a].issubset(prefix)}
    selected = {a for a in required if by_act[a] and by_act[a].issubset(selected_ids)}
    retained = {a for a in required if by_act[a] and by_act[a].issubset(rendered_ids)}
    dropped = {i["item_id"]: i["reason"] for i in context.get("dropped", [])}
    chunk_lost = [a for a in required if not by_act[a] or
                  "".join(passages[p]["text"] for p in sorted(by_act[a], key=lambda p: passages[p]["utterance_start"])) != acts[a]["text"]]
    targets = []
    for aid in sorted(required):
        act = acts[aid]
        common_terms = sorted(set(tokenize_for_index(query["query_text"])) & set(tokenize_for_index(act["text"])))
        causes = []
        required_pieces = by_act[aid]
        if prediction["execution_status"] != "SUCCESS":
            causes.append(f'EXECUTION_{prediction["execution_status"]}')
        elif aid in chunk_lost:
            causes.append("SOURCE_CHUNK_LOSS")
        elif aid not in retrieved:
            causes.append("PARTIAL_DA_RETRIEVAL" if required_pieces.intersection(prefix)
                          else "NO_SHARED_QUERY_TERMS" if not common_terms else "RANKED_OUTSIDE_TOP10")
        elif aid not in selected:
            causes.extend(sorted({f"SELECTION_{dropped.get(p, 'UNKNOWN')}" for p in required_pieces - selected_ids}))
        elif aid not in retained:
            causes.append("ASSEMBLY_RENDER_LOSS")
        targets.append({"utterance_id": aid, "speaker": act["speaker"], "text": act["text"],
            "source_member": act["source_member"], "member_sha256": act["member_sha256"],
            "native_word_ids": act["native_word_ids"], "original_hrefs": act["original_hrefs"],
            "canonical_char_start": act["char_start"], "canonical_char_end": act["char_end"],
            "retrieved": aid in retrieved, "selected": aid in selected, "in_final_context": aid in retained,
            "required_piece_ids": sorted(required_pieces),
            "retrieved_piece_ids": sorted(required_pieces.intersection(prefix)),
            "missing_retrieval_piece_ids": sorted(required_pieces - prefix),
            "retrieval_piece_coverage": len(required_pieces.intersection(prefix)) / len(required_pieces) if required_pieces else 0,
            "selected_piece_ids": sorted(required_pieces.intersection(selected_ids)),
            "rendered_piece_ids": sorted(required_pieces.intersection(rendered_ids)),
            "shared_query_terms": common_terms, "possible_causes": causes,
            "cause_limit": "lexical overlap/rank and component trace evidence; no semantic judge"})
    selection_lost = sorted(retrieved - selected)
    return {"query_id": query["query_id"], "gold_id": query["gold_summary_id"],
        "query_text": query["query_text"], "meeting_id": query["meeting_id"], "split": query["split"],
        "status": "ELIGIBLE" if required else "NO_OFFICIAL_LINKS",
        "evaluation_status": "EVALUATED" if required and prediction["execution_status"] == "SUCCESS" else "NOT_EVALUATED",
        "reason": "NO_OFFICIAL_LINKS" if not required else None if prediction["execution_status"] == "SUCCESS" else prediction["execution_status"],
        "execution_status": prediction["execution_status"],
        "metrics": official_link_metrics(ranked, required) if prediction["execution_status"] == "SUCCESS" else None,
        "required_count": len(required),
        "retrieved_count": len(retrieved), "selection_count": len(selected), "context_count": len(retained),
        "chunk_lost": sorted(chunk_lost), "selection_lost": selection_lost,
        "assembly_lost": sorted(selected - retained),
        "truncated": sorted(a for a in selected - retained if by_act[a].intersection(selected_items)),
        "selection_drop_reasons": [dropped.get(p, "UNKNOWN") for a in selection_lost for p in sorted(by_act[a] - selected_ids)],
        "correct_meeting_in_top10": any(p["metadata"]["meeting_id"] == query["meeting_id"] for p in hits),
        "meeting_scope_is_given": True, "correct_native_text_in_top10": bool(retrieved),
        "targets": targets, "top10": hits, "context_selected_ids": sorted(selected_ids),
        "context_dropped": context.get("dropped", [])}


def score(data: Path, run: Path, reference: Path, output: Path) -> dict:
    verify_seal(run)  # MUST precede every evaluator-only read.
    manifest = read_json(run / "run_manifest.json")
    inputs = data / "inputs"
    if sha((inputs / "manifest.json").read_bytes()) != manifest["input_manifest_sha256"]:
        raise ValueError("run/input manifest mismatch")
    input_manifest = read_json(inputs / "manifest.json")
    verify_files(inputs, input_manifest["files"])
    evaluator = read_json(data / "evaluator_only/manifest.json")
    verify_files(data / "evaluator_only", evaluator["files"])
    if evaluator["input_manifest_sha256"] != manifest["input_manifest_sha256"]:
        raise ValueError("qrels not bound to these inputs")
    qrels = read_rows(data / "evaluator_only/qrels.jsonl")
    queries = unique_rows(read_rows(inputs / "queries.jsonl"), "query_id")
    if len(qrels) != len(queries) or any(queries[q["query_id"]]["query_text"] != q["query_text"] for q in qrels):
        raise ValueError("query population/text mismatch")
    passages = unique_rows(read_rows(inputs / "passages.jsonl"), "piece_id")
    acts = {act["utterance_id"]: act for m in input_manifest["inventory"]
            for act in read_json(inputs / "transcripts" / f'{m["meeting_id"]}.json')["utterances"]}
    predictions = read_rows(run / "predictions.jsonl")
    keyed = {(p["method"], p["query_id"]): p for p in predictions}
    if len(keyed) != len(predictions) or any(p["query_id"] not in queries or manifest["methods"].get(p["method"], {}).get("status") != "RUN" for p in predictions):
        raise ValueError("prediction population or method mismatch")
    methods, rows_by_method = {}, {}
    for method, status in manifest["methods"].items():
        if status["status"] != "RUN":
            methods[method] = status
            continue
        rows = [evaluate_query(q, keyed.get((method, q["query_id"])), passages, acts) for q in qrels]
        encoder_truncated_pieces = set(manifest["methods"].get("dense", {}).get("encoder_truncated_piece_ids", [])) if method in {"dense", "hybrid"} else set()
        for row in rows:
            row["encoder_truncated"] = sorted({passages[p]["utterance_id"] for p in encoder_truncated_pieces
                                               if passages[p]["utterance_id"] in {t["utterance_id"] for t in row["targets"]}})
        rows_by_method[method] = rows
        methods[method] = {"status": "EVALUATED" if all(r["execution_status"] == "SUCCESS" for r in rows) else "PARTIAL_NOT_EVALUATED", "splits": {
            label: aggregate([r for r in rows if key == "all" or r["split"] == key])
            for label, key in (("dev", "dev"), ("diagnostic", "test"), ("all", "all"))}, "per_query": rows}
    verify_seal(reference)
    reference_freeze = reference.parent / "freeze_manifest.json"
    if sha(reference_freeze.read_bytes()) != input_manifest["reference_freeze_sha256"]:
        raise ValueError("reference freeze differs from prepared comparison source")
    verify_files(reference.parent, read_json(reference_freeze)["files"])
    reference_manifest = read_json(reference / "run_manifest.json")
    if reference_manifest["query_input_sha256"] != input_manifest["files"]["queries.jsonl"]:
        raise ValueError("reference query hash differs")
    reference_predictions = unique_rows(read_rows(reference / "predictions.jsonl"), "query_id")
    if set(reference_predictions) != set(queries):
        raise ValueError("reference query population differs")
    reference_rows = [{"query_id": q["query_id"], "split": q["split"],
        "metrics": official_link_metrics([h["dialogue_act_id"] for h in reference_predictions[q["query_id"]]["ranked"]],
                                         set(q["relevant_dialogue_act_ids"]))} for q in qrels]
    ref_splits = {}
    for label, key in (("dev", "dev"), ("diagnostic", "test"), ("all", "all")):
        selected = [r for r in reference_rows if key == "all" or r["split"] == key]
        eligible = [r for r in selected if r["metrics"] is not None]
        ref_splits[label] = {"scheduled": len(selected), "eligible": len(eligible), "not_evaluable": len(selected) - len(eligible),
            "official_link_metrics": {k: sum(r["metrics"][k] for r in eligible) / len(eligible) for k in eligible[0]["metrics"]}}
    failures = [(method, r) for method, rows in rows_by_method.items() for r in rows
                if r["metrics"] is not None and r["context_count"] < r["required_count"]]
    failures.sort(key=lambda pair: (pair[1]["split"] != "test", pair[1]["context_count"] / pair[1]["required_count"], pair[1]["query_id"], pair[0]))
    # Include distinct real component losses and each available method before
    # filling by severity. Selection is diagnosis only, never parameter tuning.
    priority = []
    for method in rows_by_method:
        for predicate in (lambda r: bool(r["selection_lost"]), lambda r: not r["retrieved_count"]):
            candidate = next(((m, r) for m, r in failures if m == method and predicate(r)), None)
            if candidate:
                priority.append(candidate)
    failures = priority + failures
    representatives, signatures = [], set()
    for method, row in failures:
        signature = (row["query_id"], tuple(sorted({c for t in row["targets"] for c in t["possible_causes"]})))
        if signature in signatures:
            continue
        signatures.add(signature)
        representatives.append({"method": method, **row})
        if len(representatives) == 8:
            break
    report = {"evaluation_class": "PUBLIC_HUMAN_GOLD", "version": VERSION,
        "metric_semantics": "official non-exhaustive summary-to-DA link retrieval; not all relevant speech recall",
        "split_semantics": input_manifest["series_policy"], "methods": methods,
        "reference": {"kind": "fixed rank-bm25 reference; not Product", "manifest": reference_manifest,
                      "splits": ref_splits, "per_query": reference_rows},
        "representative_failures": representatives,
        "gold_opened_after_seal_check": True, "gold_manifest_sha256": sha((data / "evaluator_only/manifest.json").read_bytes()),
        "prediction_seal_sha256": sha((run / "output_seal.json").read_bytes()),
        "scorer_sha256": sha(Path(__file__).read_bytes()),
        "inventory": input_manifest["inventory"], "provider_calls": 0,
        "not_evaluated": ["Action/Decision extraction accuracy", "F/SemanticAnalysis", "owner/deadline/absolute time",
                          "cross-meeting relations", "citation entailment", "Product E2E"]}
    write_json(output / "scores.json", report)
    write_json(output / "bad_cases.json", representatives)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reference-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.data, args.run, args.reference_run, args.output)
    print({m: info.get("splits", {}).get("diagnostic", info) for m, info in result["methods"].items()})
