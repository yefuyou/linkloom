from __future__ import annotations

import json
import subprocess
import sys

import pytest

from linkloom.evaluation.ami_public.common import validate_queries
from linkloom.evaluation.ami_public.score import official_link_metrics, aggregate


def test_official_link_metrics_count_targets_once_and_preserve_full_precision():
    metrics = official_link_metrics(["a", "a", "x", "b"], {"a", "b", "c"})
    assert metrics["recall_at_1"] == 1 / 3
    assert metrics["recall_at_5"] == 2 / 3
    assert metrics["hit_at_5"] == 1
    assert metrics["mrr_at_10"] == 1
    assert official_link_metrics(["x"] * 9 + ["a"], {"a"})["mrr_at_10"] == .1
    assert official_link_metrics(["x"] * 10 + ["a"], {"a"})["mrr_at_10"] == 0


def test_empty_official_links_are_ne_and_not_negative_or_zero_score():
    assert official_link_metrics(["a"], set()) is None
    report = aggregate([
        {"status": "ELIGIBLE", "metrics": official_link_metrics(["a"], {"a", "b"}),
         "required_count": 2, "context_count": 1, "selection_count": 1,
         "retrieved_count": 1, "chunk_lost": [], "selection_lost": [],
         "assembly_lost": [], "truncated": []},
        {"status": "NO_OFFICIAL_LINKS", "metrics": None},
    ])
    assert report["scheduled"] == 2 and report["eligible"] == 1 and report["not_evaluable"] == 1
    assert report["official_link_metrics"]["recall_at_5"] == .5
    assert report["required_evidence_coverage_micro"] == .5


def test_query_input_rejects_gold_fields_but_accepts_the_declared_question():
    row = dict(query_id="q", meeting_id="ES2002a", split="dev", query_text="Design the remote",
               query_origin="OFFICIAL_ABSTRACTIVE_SUMMARY_SENTENCE", task="retrieval")
    validate_queries([row])
    with pytest.raises(ValueError, match="schema"):
        validate_queries([{**row, "relevant_dialogue_act_ids": ["answer"]}])


def test_prediction_boundary_blocks_gold_archive_and_network_in_a_fresh_process(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    gold = tmp_path / "evaluator_only" / "qrels.jsonl"
    gold.parent.mkdir()
    gold.write_text("secret", encoding="utf8")
    script = """
from pathlib import Path
import json, socket, sys
from linkloom.evaluation.ami_public.predict import install_boundary
audit = install_boundary(Path(sys.argv[1]), Path(sys.argv[2]))
blocked=[]
for attempt in [lambda: Path(sys.argv[3]).read_bytes(),
                lambda: Path('raw.zip').read_bytes(),
                lambda: socket.create_connection(('127.0.0.1', 9))]:
    try: attempt()
    except PermissionError: blocked.append(True)
print(json.dumps({'blocked': blocked, 'audit': audit}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(root), str(tmp_path / "run"), str(gold)],
                            capture_output=True, text=True, check=True)
    assert len(json.loads(result.stdout)["blocked"]) == 3


def test_real_bm25_and_context_trace_attribute_required_evidence_loss(monkeypatch):
    from dataclasses import asdict
    from linkloom.context.assembler import ContextAssembler
    import linkloom.context.assembler as assembly_module
    from linkloom.evaluation.ami_public.common import sha
    from linkloom.evaluation.ami_public.predict import documents_for
    from linkloom.evaluation.ami_public.score import evaluate_query
    from linkloom.indexing import BM25Index
    from linkloom.retrieval_v2.models import RetrievalQuery
    from linkloom.retrieval_v2.retrievers import BM25Retriever

    passages, acts = {}, {}
    for i in range(7):
        aid, text = f"u{i}", f"alpha shared {i}"
        passages[aid] = dict(meeting_id="ES2002a", utterance_id=aid, piece_id=aid, text=text,
            text_sha256=sha(text.encode()), speaker="A", char_start=i * 20, char_end=i * 20 + len(text),
            utterance_start=0, utterance_end=len(text), canonical_sha256="c" * 64,
            source_member="dialogueActs/ES2002a.A.dialog-act.xml", member_sha256="d" * 64, archive_sha256="e" * 64)
        acts[aid] = {**passages[aid], "native_word_ids": [f"w{i}"], "original_hrefs": [f"words.xml#id(w{i})"]}
    index = BM25Index()
    index.build(documents_for(list(passages.values())))
    hits = BM25Retriever(index).retrieve(RetrievalQuery("ES2002a", "alpha", top_k=10))
    calls = []
    original_select = assembly_module.select_evidence
    def traced_select(*args, **kwargs):
        calls.append(True)
        return original_select(*args, **kwargs)
    monkeypatch.setattr(assembly_module, "select_evidence", traced_select)
    context = ContextAssembler().assemble(workspace_id="ES2002a", query="alpha", retrieved_evidence=hits)
    assert calls == [True]
    query = dict(query_id="q", gold_summary_id="g", meeting_id="ES2002a", split="dev",
                 query_text="alpha", relevant_dialogue_act_ids=["u0", "u6"])
    row = evaluate_query(query, dict(execution_status="SUCCESS", ranked=[asdict(h) for h in hits],
                                     context=asdict(context)), passages, acts)
    assert row["metrics"]["recall_at_10"] == 1
    assert row["context_count"] == 1
    assert row["selection_lost"] == ["u6"]
    assert row["selection_drop_reasons"] == ["LOW_RANK"]
    assert row["chunk_lost"] == row["assembly_lost"] == row["truncated"] == []
    import copy
    valid = dict(execution_status="SUCCESS", ranked=[asdict(h) for h in hits], context=asdict(context))
    for field, replacement in (("source_ref", "tampered://source"), ("logical_path", "/wrong/path")):
        tampered = copy.deepcopy(valid)
        tampered["ranked"][0][field] = replacement
        with pytest.raises(ValueError, match="provenance"):
            evaluate_query(query, tampered, passages, acts)
    tampered = copy.deepcopy(valid)
    evidence_item = next(i for i in tampered["context"]["selected"] if i["source_type"] == "evidence")
    evidence_item["source_refs"] = ["tampered://context"]
    with pytest.raises(ValueError, match="provenance"):
        evaluate_query(query, tampered, passages, acts)
    tampered = copy.deepcopy(valid)
    tampered["context"]["selection"]["selected_ids"] = []
    with pytest.raises(ValueError, match="selection"):
        evaluate_query(query, tampered, passages, acts)


def test_bad_prediction_seal_fails_before_any_gold_is_opened(tmp_path):
    from linkloom.evaluation.ami_public.common import write_json
    from linkloom.evaluation.ami_public.score import score
    run = tmp_path / "run"
    write_json(run / "output_seal.json", {"sealed": False})
    # No data/Gold directory exists: the seal error must occur first.
    with pytest.raises(ValueError, match="Gold remains unopened"):
        score(tmp_path / "data", run, tmp_path / "reference", tmp_path / "scores")


def test_incomplete_da_pieces_do_not_count_as_complete_source_or_context():
    from linkloom.evaluation.ami_public.common import sha
    from linkloom.evaluation.ami_public.predict import documents_for
    from linkloom.evaluation.ami_public.score import evaluate_query
    from dataclasses import asdict
    from linkloom.context.assembler import ContextAssembler
    from linkloom.retrieval_v2.models import RetrievedEvidence
    passages = {}
    for i, text in enumerate(("first ", "second")):
        pid = f"u:piece:{i}"
        passages[pid] = dict(meeting_id="ES2002a", utterance_id="u", piece_id=pid,
            text=text, text_sha256=sha(text.encode()), speaker="A", char_start=i * 6,
            char_end=i * 6 + len(text), utterance_start=i * 6, utterance_end=i * 6 + len(text),
            canonical_sha256="c" * 64, source_member="da.xml", member_sha256="d" * 64,
            archive_sha256="e" * 64)
    doc = documents_for(list(passages.values()))[1]
    hit = RetrievedEvidence(doc.evidence_id, doc.source_ref, doc.logical_path, 1.0, 1,
                            "bm25", doc.resource_id, {**doc.metadata, "workspace_id": "ES2002a"})
    bundle = ContextAssembler().assemble(workspace_id="ES2002a", query="second", retrieved_evidence=[hit])
    act = dict(utterance_id="u", text="first second", speaker="A", source_member="da.xml",
               member_sha256="d" * 64, native_word_ids=["w1", "w2"], original_hrefs=["w.xml#id(w1)..id(w2)"],
               char_start=0, char_end=12)
    row = evaluate_query(dict(query_id="q", gold_summary_id="g", query_text="second", meeting_id="ES2002a",
                             split="dev", relevant_dialogue_act_ids=["u"]),
                         dict(execution_status="SUCCESS", ranked=[asdict(hit)], context=asdict(bundle)), passages, {"u": act})
    assert row["metrics"]["recall_at_10"] == 0
    assert row["context_count"] == row["retrieved_count"] == 0
    assert row["chunk_lost"] == []
    target = row["targets"][0]
    assert target["retrieved_piece_ids"] == ["u:piece:1"]
    assert target["missing_retrieval_piece_ids"] == ["u:piece:0"]
    assert target["retrieval_piece_coverage"] == .5
    assert target["possible_causes"] == ["PARTIAL_DA_RETRIEVAL"]
    missing = evaluate_query(dict(query_id="q", gold_summary_id="g", query_text="second", meeting_id="ES2002a",
                                  split="dev", relevant_dialogue_act_ids=["u"]), None, passages, {"u": act})
    assert missing["evaluation_status"] == "NOT_EVALUATED"
    assert missing["reason"] == "NOT_STARTED"
    assert missing["metrics"] is None
    assert missing["targets"][0]["possible_causes"] == ["EXECUTION_NOT_STARTED"]


def test_frozen_reference_bytes_and_original_diagnostic_scores_are_reproducible():
    from pathlib import Path
    from linkloom.evaluation.ami_public.common import read_json, read_rows, unique_rows, verify_files
    from linkloom.evaluation.ami_public.score import verify_seal
    root = Path(__file__).resolve().parents[2] / "evals/ami_public_v1/frozen"
    verify_files(root, read_json(root / "freeze_manifest.json")["files"])
    verify_seal(root / "reference_run")
    predictions = unique_rows(read_rows(root / "reference_run/predictions.jsonl"), "query_id")
    eligible = [r for r in read_rows(root / "evaluator_only/public_human_gold/retrieval_qrels.jsonl")
                if r["split"] == "test" and r["relevant_dialogue_act_ids"]]
    metrics = [official_link_metrics([h["dialogue_act_id"] for h in predictions[r["query_id"]]["ranked"]],
                                    set(r["relevant_dialogue_act_ids"])) for r in eligible]
    assert len(eligible) == 18 and len(predictions) == 56
    assert sum(m["recall_at_5"] for m in metrics) / 18 == pytest.approx(.3009259259259259)
    assert sum(m["hit_at_5"] for m in metrics) == 8
    assert sum(m["mrr_at_10"] for m in metrics) / 18 == pytest.approx(.33818342151675485)
