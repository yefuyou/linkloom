"""Custodian only: original NXT -> separate prediction inputs and official qrels.

No module here is imported by the prediction entry point. Existing frozen
reference files are read and verified, never changed or re-sealed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

from linkloom.semantic_ingestion.ami_nxt import adapt_meeting, chunk_text
from .common import (ARCHIVE_SHA256, MEETINGS, QUERY_SHA256, VERSION, read_json,
                     read_rows, sha, transcript_projection_digest, unique_rows,
                     validate_passages, validate_queries, verify_files, write_json, write_rows)

NITE = "{http://nite.sourceforge.net/}"


def verify_official_gold(archive: Path, frozen: list[dict], utterances: dict) -> list[dict]:
    """Re-resolve human summary links from raw XML; no inferred labels."""
    expected = unique_rows(frozen, "query_id")
    provenance, verified_ids = [], set()
    with zipfile.ZipFile(archive) as bundle:
        for meeting in MEETINGS:
            summary_member = f"abstractive/{meeting}.abssumm.xml"
            link_member = f"extractive/{meeting}.summlink.xml"
            summary_bytes, link_bytes = bundle.read(summary_member), bundle.read(link_member)
            summary_root, link_root = ET.fromstring(summary_bytes), ET.fromstring(link_bytes)
            summaries = {s.attrib[NITE + "id"]: (section.tag, "".join(s.itertext()).strip())
                         for section in summary_root for s in section.findall("sentence")
                         if section.tag in {"actions", "decisions"}}
            links = defaultdict(set)
            for link in link_root.findall("summlink"):
                pointers = {p.attrib["role"]: p.attrib["href"] for p in link.findall(NITE + "pointer")}
                if set(pointers) != {"abstractive", "extractive"}:
                    raise ValueError("unrecognized official summary link roles")
                # This corpus's summary pointers are single native IDs. Never
                # silently interpret a new range protocol as a single endpoint.
                parsed = {role: re.fullmatch(r"([^#]+)#id\(([^)]+)\)", href)
                          for role, href in pointers.items()}
                if not all(parsed.values()):
                    raise ValueError("unsupported official summary pointer range")
                sid = parsed["abstractive"].group(2)
                aid = parsed["extractive"].group(2)
                if sid not in summaries:
                    continue
                if parsed["abstractive"].group(1) != f"{meeting}.abssumm.xml":
                    raise ValueError("summary pointer member mismatch")
                act = utterances[aid]
                if Path(act["source_member"]).name != parsed["extractive"].group(1):
                    raise ValueError("DA pointer member mismatch")
                links[sid].add(aid)
                provenance.append({"query_id": f"{meeting}:{sid}", "utterance_id": aid,
                    "section": summaries[sid][0], "annotation_id": link.attrib[NITE + "id"],
                    "annotation_member": link_member, "annotation_sha256": sha(link_bytes),
                    "summary_member": summary_member, "summary_sha256": sha(summary_bytes),
                    "original_pointers": pointers})
            for sid, (_section, text) in summaries.items():
                verified_ids.add(f"{meeting}:{sid}")
                gold = expected[f"{meeting}:{sid}"]
                if gold["query_text"] != text or set(gold["relevant_dialogue_act_ids"]) != links[sid]:
                    raise ValueError("frozen qrels disagree with original human annotations")
    if verified_ids != set(expected) or len(expected) != 56:
        raise ValueError("unexpected official query population")
    return provenance


def validate_mapping(adapted: dict) -> dict:
    """Audit every actual source span and chunk, not just a small fixture."""
    canonical = adapted["canonical_text"]
    if sha(canonical.encode("utf-8")) != adapted["canonical_sha256"]:
        raise ValueError("canonical hash mismatch")
    if "".join(chunk["text"] for chunk in adapted["chunks"]) != canonical:
        raise ValueError("chunk roundtrip mismatch")
    cursor = 0
    for chunk in adapted["chunks"]:
        if (chunk["start"] != cursor or canonical[chunk["start"]:chunk["end"]] != chunk["text"]
            or sha(chunk["text"].encode("utf-8")) != chunk["sha256"]
            or len(chunk["text"]) > 8000 or len(chunk["text"].encode("utf-8")) > 16000):
            raise ValueError("chunk position/hash/budget mismatch")
        cursor = chunk["end"]
    words = unique_rows(adapted["words"], "native_word_id")
    seen, spans = set(), 0
    for act in adapted["utterances"]:
        if canonical[act["char_start"]:act["char_end"]] != act["text"]:
            raise ValueError("utterance source position mismatch")
        for span in act["word_spans"]:
            word = words[span["native_word_id"]]
            if (canonical[span["start"]:span["end"]] != word["text"] or span["text"] != word["text"]
                or span["text_sha256"] != sha(word["text"].encode("utf-8"))
                or span["member_sha256"] != adapted["members"][word["source_member"]]):
                raise ValueError("native word source position/hash mismatch")
            seen.add(span["native_word_id"])
            spans += 1
    if seen != set(words):
        raise ValueError("source words lost in canonical representation")
    return {"native_words_checked": len(words), "word_spans_checked": spans,
            "chunks_checked": len(adapted["chunks"]), "all_positions_hashes_and_budgets_valid": True}


def prepare(archive: Path, reference: Path, output: Path) -> dict:
    if sha(archive.read_bytes()) != ARCHIVE_SHA256:
        raise ValueError("official archive hash mismatch")
    if output.exists():
        raise FileExistsError("preparation requires a new output directory")
    freeze = read_json(reference / "freeze_manifest.json")
    verify_files(reference, freeze["files"])
    projections = read_json(reference / "corpus_projection.json")
    queries_path = reference / "query_inputs/retrieval_queries.jsonl"
    if sha(queries_path.read_bytes()) != QUERY_SHA256:
        raise ValueError("fixed query bytes changed")
    queries = read_rows(queries_path)
    validate_queries(queries)
    qrels = read_rows(reference / "evaluator_only/public_human_gold/retrieval_qrels.jsonl")
    if len(qrels) != 56 or sum(bool(q["relevant_dialogue_act_ids"]) for q in qrels) != 51:
        raise ValueError("unexpected official eligible/N/E counts")
    if [{k: q[k] for k in queries[0]} for q in qrels] != queries:
        raise ValueError("query declarations differ from frozen qrels")
    passages, all_utterances, inventory, file_hashes = [], {}, [], {}
    output.mkdir(parents=True)
    input_root = output / "inputs"
    for meeting in MEETINGS:
        adapted = adapt_meeting(archive, meeting, expected_sha256=ARCHIVE_SHA256)
        validation = validate_mapping(adapted)
        split = "dev" if meeting.startswith("ES2002") else "test"
        acts = [act for act in adapted["utterances"] if not act["is_unsegmented"]]
        if transcript_projection_digest(acts, "utterance_id") != projections[meeting]["projection_sha256"]:
            raise ValueError("adapter/reference DA universe/text/native words mismatch")
        for act in acts:
            all_utterances[act["utterance_id"]] = act
            pieces = chunk_text(act["text"]) if act["text"] else [{"text": "", "char_start": 0, "char_end": 0}]
            for index, piece in enumerate(pieces):
                begin, end = piece.get("char_start", piece.get("start")), piece.get("char_end", piece.get("end"))
                passages.append({"meeting_id": meeting, "utterance_id": act["utterance_id"],
                    "piece_id": act["utterance_id"] if len(pieces) == 1 else f'{act["utterance_id"]}:piece:{index:04d}',
                    "text": piece["text"], "text_sha256": sha(piece["text"].encode("utf-8")),
                    "speaker": act["speaker"], "char_start": act["char_start"] + begin,
                    "char_end": act["char_start"] + end, "utterance_start": begin, "utterance_end": end,
                    "canonical_sha256": adapted["canonical_sha256"], "source_member": act["source_member"],
                    "member_sha256": act["member_sha256"], "archive_sha256": ARCHIVE_SHA256})
        # Rich reverse mapping stays transcript-only. Only the small passage
        # whitelist and query file are read by prediction.
        write_json(input_root / "transcripts" / f"{meeting}.json", adapted)
        file_hashes.update({f"transcripts/{meeting}.json": sha((input_root / "transcripts" / f"{meeting}.json").read_bytes())})
        inventory.append({"meeting_id": meeting, "series_id": meeting[:-1], "split": split,
                          "words": len(adapted["words"]), "dialogue_acts": len(acts),
                          "unsegmented_utterances": adapted["counts"]["unsegmented_utterances"],
                          "unsegmented_words": adapted["counts"]["unsegmented_words"],
                          "nontext_events": len(adapted["nontext_events"]),
                          "empty_dialogue_acts": sum(not a["text"] for a in acts),
                          "batch_chunks": len(adapted["chunks"]), "members": adapted["members"],
                          "mapping_validation": validation,
                          "canonical_sha256": adapted["canonical_sha256"]})
    if sum(x["words"] for x in inventory) != 49806 or len(all_utterances) != 6981:
        raise ValueError("official corpus count mismatch")
    validate_passages(passages)
    provenance = verify_official_gold(archive, qrels, all_utterances)
    write_rows(input_root / "passages.jsonl", passages)
    # Preserve the exact already-frozen query bytes, not a reserialization.
    shutil.copyfile(queries_path, input_root / "queries.jsonl")
    file_hashes.update({name: sha((input_root / name).read_bytes()) for name in ("passages.jsonl", "queries.jsonl")})
    manifest = {"version": VERSION, "archive_sha256": ARCHIVE_SHA256,
        "source_url": "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip",
        "license": "CC BY 4.0; official LICENCE.txt retained in custodian metadata",
        "archive_filename_version": "1.6.2", "internal_readme_release": "1.7 (2014-06-16)",
        "files": file_hashes, "inventory": inventory, "queries": len(queries), "passages": len(passages),
        "series_policy": "ES2002 dev; ES2005 previously exposed diagnostic (legacy key test); both AMI SA train",
        "candidate_scope": "all native same-meeting dialogue acts; no Gold-based filtering; words outside native DAs retained in canonical/F batches but outside this reference-matched DA benchmark",
        "query_protocol": "fixed official summary sentence is declared query text; no sections/links/qrels in prediction",
        "normalization": "native word text preserved; ASCII single-space join; deterministic DA ordering",
        "reference_freeze_sha256": sha((reference / "freeze_manifest.json").read_bytes()),
        "retrieval_settings": {"top_k": 10, "bm25": "existing BM25Index defaults, unchanged tokenizer",
            "dense": "existing SentenceTransformerEmbedder default; local cache only",
            "hybrid": "existing HybridRetriever candidate_k=20 rrf_k=60",
            "context": "existing ContextAssembler/ContextBudget/ContextPolicy defaults; no Gold groups"}}
    write_json(input_root / "manifest.json", manifest)
    write_rows(output / "evaluator_only/qrels.jsonl", qrels)
    write_rows(output / "evaluator_only/provenance.jsonl", provenance)
    with zipfile.ZipFile(archive) as bundle:
        write_json(output / "evaluator_only/upstream_metadata.json",
            {name: {"sha256": sha(bundle.read(name)), "text": bundle.read(name).decode("utf-8", errors="replace")}
             for name in ("LICENCE.txt", "00README_MANUAL.txt")})
    write_json(output / "evaluator_only/manifest.json", {"evaluation_class": "PUBLIC_HUMAN_GOLD",
        "mapping_version": "ami-official-summary-links/v1", "source_reverified": True,
        "eligible": 51, "not_evaluable": 5, "files": {name: sha((output / "evaluator_only" / name).read_bytes())
          for name in ("qrels.jsonl", "provenance.jsonl", "upstream_metadata.json")},
        "input_manifest_sha256": sha((input_root / "manifest.json").read_bytes())})
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.archive, args.reference_root, args.output)
    print({"meetings": len(result["inventory"]), "queries": result["queries"], "passages": result["passages"]})
