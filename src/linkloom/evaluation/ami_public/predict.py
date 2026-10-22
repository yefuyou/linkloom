"""Gold-blind, network-disabled component run using LinkLoom's real retrievers.

Usage: python -m linkloom.evaluation.ami_public.predict --inputs ... --output ...
The archive, qrels, and custodian reference root are deliberately not arguments.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys

from linkloom.context.assembler import ContextAssembler
from linkloom.indexing import BM25Index, IndexDocument
from linkloom.retrieval_v2.models import RetrievalQuery
from linkloom.retrieval_v2.retrievers import BM25Retriever
from .common import (VERSION, passage_logical_path, passage_source_ref, read_json, read_rows, sha, validate_passages,
                     validate_queries, verify_files, write_json, write_rows)


def install_boundary(inputs: Path, output: Path) -> dict:
    """Audited Python file/network boundary, explicitly not an OS sandbox."""
    inputs, output = inputs.resolve(), output.resolve()
    audit = {"input_files_opened": [], "denied_data_opens": 0, "denied_files": [], "network_attempts": 0,
             "allowed_stdlib_zip_probes": 0,
             "kind": "Python audit hook + strict payload whitelist; not OS sandbox"}
    allowed = {inputs / name for name in ("passages.jsonl", "queries.jsonl", "manifest.json")}
    # Python's normal import machinery probes this interpreter-owned archive,
    # often even when it does not exist. It is neither an AMI source archive
    # nor a caller-selected exception. All other ZIP data remain unavailable.
    stdlib_zip = (Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.zip").resolve()

    def hook(event, args):
        if event == "socket.connect":
            audit["network_attempts"] += 1
            raise PermissionError("network is disabled during AMI prediction")
        if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        parts = {part.casefold() for part in path.parts}
        forbidden = bool(parts & {"evaluator_only", "selected_raw", "upstream"})
        if path == stdlib_zip:
            audit["allowed_stdlib_zip_probes"] += 1
        forbidden |= path.suffix.casefold() == ".zip" and path != stdlib_zip
        forbidden |= path.name.casefold().endswith((".abssumm.xml", ".summlink.xml", ".extsumm.xml"))
        if path.is_relative_to(inputs):
            forbidden |= path not in allowed
            mode = args[1] or ""
            forbidden |= isinstance(mode, str) and any(x in mode for x in "wax+")
            if not forbidden and path.name not in audit["input_files_opened"]:
                audit["input_files_opened"].append(path.name)
        if forbidden:
            audit["denied_data_opens"] += 1
            audit["denied_files"].append(path.name)
            raise PermissionError("Gold/raw data or unapproved input access rejected")

    sys.addaudithook(hook)
    return audit


def documents_for(passages: list[dict]) -> list[IndexDocument]:
    return [IndexDocument(workspace_id=p["meeting_id"], resource_id=p["piece_id"],
        evidence_id=p["piece_id"], logical_path=passage_logical_path(p),
        content=p["text"],
        source_ref=passage_source_ref(p),
        metadata={**p, "content_hash": p["canonical_sha256"], "quote_sha256": p["text_sha256"]})
        for p in passages]


def local_dense(documents: list[IndexDocument]):
    """Existing local embedding/index implementation; no substitute model."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import snapshot_download
    from linkloom.indexing import SentenceTransformerEmbedder, VectorIndex
    from linkloom.retrieval_v2.retrievers import DenseRetriever

    model_id = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    snapshot = Path(snapshot_download(model_id, local_files_only=True))
    model_files = {p.relative_to(snapshot).as_posix(): sha(p.read_bytes())
                   for p in sorted(snapshot.rglob("*")) if p.is_file()}
    import torch
    torch.set_num_threads(4)
    embedder = SentenceTransformerEmbedder(str(snapshot))
    index = VectorIndex(embedder)
    index.build(documents)
    # Observe the existing encoder's truncation boundary; do not change it.
    model = embedder._model
    max_length = model.max_seq_length
    truncated = [doc.resource_id for doc in documents
                 if len(model.tokenizer(doc.content, truncation=False)["input_ids"]) > max_length]
    return DenseRetriever(index), {"model_id": model_id, "snapshot_revision": snapshot.name,
        "model_files_sha256": model_files, "max_seq_length": max_length,
        "encoder_truncated_piece_ids": truncated, "torch_threads": 4,
        "sentence_transformers_version": importlib.metadata.version("sentence-transformers")}


def code_hashes() -> dict:
    root = Path(__file__).resolve().parents[3]
    names = [p for folder in ("evaluation/ami_public", "retrieval_v2", "indexing", "context")
             for p in (root / "linkloom" / folder).glob("*.py")]
    names.append(root / "linkloom/semantic_ingestion/ami_nxt.py")
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(names)}


def predict(inputs: Path, output: Path) -> dict:
    inputs, output = inputs.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError("prediction requires a new run directory")
    # Operational Git metadata only; no evaluator arguments are present.
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    fingerprints = code_hashes()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    audit = install_boundary(inputs, output)
    manifest = read_json(inputs / "manifest.json")
    verify_files(inputs, {name: manifest["files"][name] for name in ("queries.jsonl", "passages.jsonl")})
    passages, queries = read_rows(inputs / "passages.jsonl"), read_rows(inputs / "queries.jsonl")
    validate_queries(queries)
    validate_passages(passages)
    documents = documents_for(passages)
    index = BM25Index()
    index.build(documents)
    bm25 = BM25Retriever(index)
    retrievers, statuses = {"bm25": bm25}, {"bm25": {"status": "RUN"}}
    try:
        print("Loading cached local Dense index (network disabled)", flush=True)
        dense, dense_manifest = local_dense(documents)
        from linkloom.retrieval_v2.retrievers import HybridRetriever
        retrievers.update({"dense": dense, "hybrid": HybridRetriever(bm25, dense)})
        statuses.update({"dense": {"status": "RUN", **dense_manifest},
                         "hybrid": {"status": "RUN", "candidate_k": 20, "rrf_k": 60}})
    except Exception as error:
        reason = f"local Dense loading/index failed: {type(error).__name__}: {error}"
        statuses.update({"dense": {"status": "NOT_RUN", "reason": reason},
                         "hybrid": {"status": "NOT_RUN", "reason": reason}})
    assembler = ContextAssembler()
    predictions = []
    for method, retriever in retrievers.items():
        for query in queries:
            hits = retriever.retrieve(RetrievalQuery(query["meeting_id"], query["query_text"], top_k=10))
            bundle = assembler.assemble(workspace_id=query["meeting_id"], query=query["query_text"],
                                        retrieved_evidence=hits)
            predictions.append({"method": method, "query_id": query["query_id"],
                "meeting_id": query["meeting_id"], "split": query["split"],
                "execution_status": "SUCCESS", "ranked": [asdict(hit) for hit in hits],
                "context": asdict(bundle)})
        print(f"{method}: {len(queries)} queries completed", flush=True)
    output.mkdir(parents=True)
    write_rows(output / "predictions.jsonl", predictions)
    if audit["network_attempts"] or audit["denied_data_opens"]:
        write_json(output / "failed_isolation.json", audit)
        raise ValueError("prediction isolation violation; output not sealed")
    run_manifest = {"version": VERSION, "git_head_at_run": git_head,
        "code_sha256": fingerprints, "input_manifest_sha256": sha((inputs / "manifest.json").read_bytes()),
        "input_files": {name: manifest["files"][name] for name in ("queries.jsonl", "passages.jsonl")},
        "evaluation_class": "PUBLIC_HUMAN_GOLD", "component_run": True,
        "not_product_e2e": True, "settings": manifest["retrieval_settings"],
        "query_count": len(queries), "passage_count": len(passages), "methods": statuses,
        "rank_bm25_version": importlib.metadata.version("rank-bm25"),
        "python_version": sys.version, "isolation": audit,
        "pipeline": {"retrievers": {key: f"{type(obj).__module__}.{type(obj).__name__}" for key, obj in retrievers.items()},
                     "assembler": "linkloom.context.assembler.ContextAssembler",
                     "selector": "linkloom.context.selection.select_evidence",
                     "budget": asdict(assembler.budget)},
        "provider_calls": 0, "provider_cost_usd": 0}
    write_json(output / "run_manifest.json", run_manifest)
    write_json(output / "output_seal.json", {"sealed": True,
        "predictions_sha256": sha((output / "predictions.jsonl").read_bytes()),
        "run_manifest_sha256": sha((output / "run_manifest.json").read_bytes())})
    return run_manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = predict(args.inputs, args.output)
    print({key: value["status"] for key, value in result["methods"].items()})
