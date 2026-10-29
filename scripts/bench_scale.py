"""Offline scale measurements of existing LinkLoom components; never calls an LLM.

Run: python -B scripts/bench_scale.py
Requires the existing project dependencies, psutil, and the retrieval extra.
The embedding model must already exist in the local Hugging Face cache.
All output goes into a new directory; existing files are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import gc
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from bench_scale_data import (MODEL, SEED, SIZES, WORKSPACE, fixture_manifest,
                              retrieval_fixture, routing_fixture, temporal_cases,
                              temporal_rows)


def offline_only():
    """Fail closed on Python network connections; Hugging Face is cache-only."""
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", WANDB_DISABLED="true",
                      CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
    def denied(*args, **kwargs):
        raise RuntimeError("network access is disabled in bench_scale")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def percentiles(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no latency samples")
    def at(fraction):
        pos = (len(ordered) - 1) * fraction
        low, high = math.floor(pos), math.ceil(pos)
        return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)
    return {"p50_ms": at(.50), "p95_ms": at(.95), "samples": len(ordered)}


class RSS:
    """Sample OS resident set, without tracemalloc overhead in timed operations."""
    def __init__(self):
        import psutil
        self.process = psutil.Process()
        self.baseline = self.process.memory_info().rss
        self.peak = self.baseline
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.stop.wait(.01):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.resident = self.process.memory_info().rss
        self.peak = max(self.peak, self.resident)

    def report(self):
        mib = 1024 ** 2
        return {"baseline_rss_mib": self.baseline / mib,
                "resident_rss_mib": self.resident / mib,
                "peak_rss_mib": self.peak / mib,
                "peak_delta_rss_mib": (self.peak - self.baseline) / mib,
                "resident_delta_rss_mib": (self.resident - self.baseline) / mib,
                "sample_interval_ms": 10}


def benchmark_retrieval(mode, size, query_count, model):
    from linkloom.indexing import BM25Index, VectorIndex, SentenceTransformerEmbedder
    from linkloom.indexing.models import IndexDocument
    from linkloom.retrieval_v2.models import RetrievalQuery
    from linkloom.retrieval_v2.retrievers import BM25Retriever, DenseRetriever, HybridRetriever
    raw, queries = retrieval_fixture(size, query_count)
    manifest = fixture_manifest(raw, queries)
    documents = [IndexDocument(**row) for row in raw]
    del raw
    gc.collect()
    timings = {}
    with RSS() as memory:
        start = time.perf_counter()
        lexical = BM25Index()
        lexical_start = time.perf_counter()
        lexical.build(documents)
        timings["bm25_build_s"] = time.perf_counter() - lexical_start
        retriever = BM25Retriever(lexical)
        if mode == "hybrid":
            # Cache lookup never downloads. The existing embedder receives a
            # local snapshot path, and retains its normal encoding behavior.
            from huggingface_hub import snapshot_download
            local_model = (model if Path(model).is_dir() else
                           snapshot_download(model, local_files_only=True))
            dense = VectorIndex(SentenceTransformerEmbedder(str(local_model)))
            print(f"hybrid {size}: embedding documents on CPU", file=sys.stderr, flush=True)
            dense.build(documents)
            retriever = HybridRetriever(retriever, DenseRetriever(dense))
            timings.update(model_load_s=dense.last_model_load_ms / 1000,
                           document_embedding_s=dense.last_document_embedding_ms / 1000)
        timings["build_s"] = time.perf_counter() - start
    # Build and query peaks are separately measured. Warm-up is outside samples.
    for query in queries[:5]:
        retriever.retrieve(RetrievalQuery(WORKSPACE, query["query"], top_k=5))
    details, latencies, stage_times = [], [], {}
    with RSS() as query_memory:
        for query in queries:
            begin = time.perf_counter()
            hits = retriever.retrieve(RetrievalQuery(WORKSPACE, query["query"], top_k=5))
            elapsed = (time.perf_counter() - begin) * 1000
            latencies.append(elapsed)
            ids = [hit.resource_id for hit in hits]
            recall = len(set(query["relevant_ids"]) & set(ids)) / len(query["relevant_ids"])
            details.append({**query, "retrieved_ids": ids, "recall_at_5": recall,
                            "latency_ms": elapsed})
            for key, value in getattr(retriever, "last_stage_timings_ms", {}).items():
                stage_times.setdefault(key, []).append(value)
    return {"component": mode, "size": size, "status": "measured",
            "fixture": manifest, **timings, "memory": memory.report(),
            "query_memory": query_memory.report(), **percentiles(latencies),
            "recall_at_5": sum(row["recall_at_5"] for row in details) / len(details),
            "recall_by_kind": {kind: sum(r["recall_at_5"] for r in details if r["kind"] == kind) /
                               sum(r["kind"] == kind for r in details)
                               for kind in sorted({r["kind"] for r in details})},
            "stage_latency": {k: percentiles(v) for k, v in stage_times.items()},
            "details": details}


def explain_calls(connection, call):
    """Capture the actual parameter-expanded SELECTs issued by a public API."""
    statements = []
    connection.set_trace_callback(lambda sql: statements.append(sql)
                                  if sql.lstrip().upper().startswith("SELECT") else None)
    try:
        call()
    finally:
        connection.set_trace_callback(None)
    plans = []
    for sql in dict.fromkeys(statements):
        detail = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)]
        plans.append({"sql": sql.strip(), "plan": detail})
    return plans


def benchmark_temporal(directory, query_count):
    from linkloom.decision_memory import TemporalDecisionStore
    store = TemporalDecisionStore(directory / "synthetic-decisions.sqlite3")
    connection = store._connection
    # Bulk seed only the synthetic database using the existing schema/indexes.
    # This measures read paths, not write policy, materialization, or review.
    columns = ("decision_id,workspace_id,subject_key,subject,relation,"
               "subject_normalized,relation_normalized,value,status,memory_state,"
               "valid_from,valid_to,supersedes_id,source_episode_id,provenance_run_id")
    start = time.perf_counter()
    with connection:
        connection.executemany(f"INSERT INTO decision ({columns}) VALUES ({','.join('?' for _ in range(15))})",
                               temporal_rows())
        connection.executemany(
            "INSERT INTO decision_evidence (decision_id,ordinal,evidence_ref) VALUES (?,0,?)",
            ((f"decision-{i:06d}", f"synthetic/decision-{i:06d}.md#L1") for i in range(100_000)))
    seed_s = time.perf_counter() - start
    assert connection.execute("SELECT count(*) FROM decision").fetchone()[0] == 100_000
    cases = list(temporal_cases(query_count=max(query_count, 300)))
    rows = []
    try:
        for api in ("get_as_of", "lookup"):
            def call(case):
                if api == "get_as_of":
                    record = store.get_as_of(WORKSPACE, case["subject_key"], case["as_of"])
                    return [record.decision_id] if record else []
                result = store.lookup(WORKSPACE, case["subject"], "uses supplier", as_of=case["as_of"])
                return [record.decision_id for record in result.records]
            for case in cases[:6]:
                call(case)
            latencies, errors = [], []
            for case in cases:
                begin = time.perf_counter()
                actual = call(case)
                latencies.append((time.perf_counter() - begin) * 1000)
                expected = [case["expected_id"]] if case["expected_id"] else []
                if actual != expected:
                    errors.append({"as_of": case["as_of"].isoformat(), "expected": expected, "actual": actual})
            plans = explain_calls(connection, lambda: call(cases[2]))
            decision_plans = [p for p in plans if "FROM decision " in " ".join(p["sql"].split())
                              and "decision_evidence" not in p["sql"]]
            uses_index = bool(decision_plans) and all(
                any("SEARCH decision USING" in detail and "INDEX" in detail for detail in p["plan"])
                and not any("SCAN decision" in detail for detail in p["plan"])
                for p in decision_plans)
            if errors or not uses_index:
                raise AssertionError({"api": api, "errors": errors, "plans": plans})
            rows.append({"component": f"temporal/{api}", "size": 100_000,
                         "status": "measured", "seed_s": seed_s,
                         **percentiles(latencies), "correct": len(cases), "errors": errors,
                         "uses_index": uses_index, "explain": plans})
    finally:
        store.close()
    return rows


def benchmark_routing(count):
    from linkloom.semantic_ingestion.relations import FrozenRelationResolver
    from linkloom.ui.scope_discovery import discover_scope
    slots, queries = routing_fixture(count)
    resolver = FrozenRelationResolver(("uses supplier",))
    for case in queries[:5]:
        discover_scope(case["query"], slots, resolver)
    details, latencies = [], []
    for case in queries:
        start = time.perf_counter()
        result = discover_scope(case["query"], slots, resolver)
        elapsed = (time.perf_counter() - start) * 1000
        latencies.append(elapsed)
        candidates = {slot["subject"] for slot in result["candidates"]}
        eligible = set(case["eligible"])
        selected = (result.get("scope") or {}).get("subject")
        wrong = selected is not None and (case["must_abstain"] or selected not in eligible)
        recall = len(candidates & eligible) / len(eligible) if eligible else None
        details.append({**case, "status": result["status"], "selected": selected,
                        "candidate_subjects": sorted(candidates),
                        "candidate_count_before_top5": result["candidate_count"],
                        "candidate_recall_at_5": recall, "wrong_binding": wrong,
                        "latency_ms": elapsed})
    scored = [row for row in details if row["candidate_recall_at_5"] is not None]
    unique = [row for row in scored if not row["must_abstain"]]
    return {"component": "routing", "size": count, "status": "measured",
            **percentiles(latencies),
            "candidate_recall_at_5": sum(row["candidate_recall_at_5"] for row in scored) / len(scored),
            "unique_target_recall_at_5": sum(row["candidate_recall_at_5"] for row in unique) / len(unique),
            "wrong_bindings": sum(row["wrong_binding"] for row in details),
            "queries": len(details), "negative_or_ambiguous_queries": sum(r["must_abstain"] for r in details),
            "status_counts": dict(Counter(row["status"] for row in details)),
            "by_kind": {kind: {
                "queries": len(group := [r for r in details if r["kind"] == kind]),
                "wrong_bindings": sum(r["wrong_binding"] for r in group),
                "recall_at_5": (sum(r["candidate_recall_at_5"] for r in group) / len(group)
                                if group[0]["candidate_recall_at_5"] is not None else None)}
                for kind in sorted({r["kind"] for r in details})}, "details": details}


def machine():
    import psutil
    cpu = platform.processor()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    versions = {}
    for package in ("numpy", "rank-bm25", "psutil", "sentence-transformers", "torch"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    return {"cpu": cpu, "physical_cores": psutil.cpu_count(logical=False),
            "logical_cpus": psutil.cpu_count(), "ram_gib": psutil.virtual_memory().total / 1024 ** 3,
            "available_ram_gib_at_start": psutil.virtual_memory().available / 1024 ** 3,
            "os": platform.platform(), "python": sys.version, "packages": versions,
            "commit": git("log", "-1", "--oneline"), "branch": git("branch", "--show-current"),
            "embedding_device": "CPU", "model": MODEL}


def worker_command(args, mode, size):
    return [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", mode,
            "--size", str(size), "--queries", str(args.queries), "--model", args.model,
            "--output", str(args.output)]


def render(report):
    host = report["machine"]
    lines = ["# Offline scale benchmark", "",
             f"Commit: {host['commit']}; branch: {host['branch']}.",
             f"CPU: {host['cpu']} ({host['physical_cores']} cores/{host['logical_cpus']} threads); "
             f"RAM: {host['ram_gib']:.2f} GiB; {host['os']}; Python {host['python'].split()[0]}.",
             f"Available RAM at start: {host['available_ram_gib_at_start']:.2f} GiB; measurements use the current desktop load.",
             f"Embedding: {report['model']} on CPU, existing local cache only.", "",
             "| Component | Scale | Build s | Peak RSS MiB (build delta) | p50 ms | p95 ms | Recall@5 / candidate recall@5 | Wrong bindings |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["rows"]:
        if row["status"] != "measured":
            lines.append(f"| {row['component']} | {row['size']:,} | skipped | — | — | — | — | — |")
            continue
        memory = row.get("memory")
        memory_text = f"{memory['peak_rss_mib']:.1f} (+{memory['peak_delta_rss_mib']:.1f})" if memory else "—"
        recall = row.get("recall_at_5", row.get("candidate_recall_at_5"))
        recall_text = f"{recall:.2%}" if recall is not None else "—"
        build = f"{row['build_s']:.3f}" if "build_s" in row else "—"
        lines.append(f"| {row['component']} | {row['size']:,} | {build} | {memory_text} | "
                     f"{row['p50_ms']:.3f} | {row['p95_ms']:.3f} | {recall_text} | {row.get('wrong_bindings', '—')} |")
    lines.extend(["", "## Measurement definitions", *["", *report["methodology"]]])
    for row in report["rows"]:
        if row["status"] == "skipped":
            lines.extend(["", f"Skipped {row['component']} {row['size']}: {row['reason']}"])
        if row.get("uses_index"):
            lines.extend(["", f"### {row['component']}: EXPLAIN QUERY PLAN"])
            for plan in row["explain"]:
                lines.extend(["", "~~~sql", plan["sql"], "~~~", *[f"- {item}" for item in plan["plan"]]])
        if row["component"] == "routing" and row["status"] == "measured":
            lines.extend(["", f"Routing {row['size']}: unique-target candidate recall@5 "
                          f"{row['unique_target_recall_at_5']:.2%}; "
                          f"{row['queries']} queries, {row['negative_or_ambiguous_queries']} require abstention."])
    measured = [row for row in report["rows"] if row["status"] == "measured"]
    hybrids = sorted((r for r in measured if r["component"] == "hybrid"), key=lambda r: r["size"])
    lexical = sorted((r for r in measured if r["component"] == "bm25"), key=lambda r: r["size"])
    routing = sorted((r for r in measured if r["component"] == "routing"), key=lambda r: r["size"])
    if hybrids:
        first, largest = hybrids[0], hybrids[-1]
        lines.extend(["", "## Observed bottlenecks", "",
            f"The first measured hybrid scale ({first['size']:,}) already takes {first['build_s']:.2f}s to build, "
            f"including {first['model_load_s']:.2f}s model load and {first['document_embedding_s']:.2f}s document embedding. "
            f"At {largest['size']:,}, embedding alone takes {largest['document_embedding_s']:.2f}s "
            f"out of {largest['build_s']:.2f}s build time. These timings identify embedding/model startup as the earliest build cost."])
    if lexical:
        largest = lexical[-1]
        lines.extend(["",
            f"BM25 at {largest['size']:,}: build {largest['build_s']:.2f}s, "
            f"query p95 {largest['p95_ms']:.2f}ms, build peak RSS {largest['memory']['peak_rss_mib']:.1f} MiB. "
            "The existing code constructs scope indexes and scores/sorts document candidates; no implementation was changed."])
    if routing:
        largest = routing[-1]
        cross = largest["by_kind"]["cross_project"]["recall_at_5"]
        lines.extend(["",
            f"Routing at {largest['size']:,} slots: p95 {largest['p95_ms']:.2f}ms; "
            f"unique-target recall {largest['unique_target_recall_at_5']:.2%}; wrong bindings {largest['wrong_bindings']}. "
            f"Cross-project candidate recall@5 is {cross:.2%}. "
            "Same-name-only questions return no candidates in this fixture; conflicting project scopes expose "
            "a broad ambiguous candidate set truncated to five, reducing candidate recall while abstaining from binding."])
    lines.extend(["", "## Reproduce", "", "~~~powershell", report["reproduce"], "~~~", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "work" / ("scale-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")))
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES), choices=SIZES)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--hybrid-100k-embedding-limit-s", type=float, default=300)
    parser.add_argument("--worker", choices=("bm25", "hybrid", "temporal", "routing"))
    parser.add_argument("--size", type=int)
    args = parser.parse_args()
    if not 1 <= args.queries <= 200:
        parser.error("--queries must be between 1 and 200")
    offline_only()
    if args.worker:
        if args.worker in ("bm25", "hybrid"):
            result = benchmark_retrieval(args.worker, args.size, args.queries, args.model)
        elif args.worker == "temporal":
            result = benchmark_temporal(args.output, args.queries)
        else:
            result = benchmark_routing(args.size)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(UTC).isoformat(), "seed": SEED, "model": args.model,
              "machine": machine(), "rows": [], "methodology": [
                  "- Existing BM25Index, VectorIndex, HybridRetriever, TemporalDecisionStore and discover_scope are called unchanged. No LLM or remote provider.",
                  "- Python network connections are denied; Hugging Face/Transformers offline flags are set; local embeddings run on CPU. No generated corpus is a real vault.",
                  "- Each measured component/scale runs in a fresh process, sequentially. Corpus generation and imports of retrieval components precede the RSS baseline. Hybrid build includes first model import/load, BM25 build, embeddings and normalization; model load and embedding time are separate JSON fields.",
                  "- Build peak RSS is total process RSS sampled every 10 ms; parentheses show peak minus post-corpus baseline, including the local embedding runtime/model for hybrid. Query peak/resident RSS are also saved in JSON. These are resident bytes, not Python-only allocations.",
                  f"- Five warm-up queries; {args.queries} distinct standard retrieval queries ({(args.queries + 1) // 2} current/{args.queries // 2} historical), one gold document each, same questions at every scale; p50/p95 use linear interpolation. Query latency includes query embedding and fusion. Build timings are one run, not repeated-run distributions.",
                  "- Every 10-document group has 1 approved current, 1 approved superseded, 6 unapproved near duplicates and 2 same-name records in other projects. Corpus hashes, answer keys, retrieved IDs and per-query measurements are saved in JSON.",
                  "- Temporal: 100,000 decisions, 1,000 slots x 100 versions, on-disk SQLite with existing indexes. Bulk synthetic seeding is untimed; 300 warm-cache API queries include historical/current/boundary/prehistory cases and evidence reconstruction. EXPLAIN uses captured SQL from those exact APIs, without INDEXED BY or added indexes.",
                  "- Routing: reviewed, verified synthetic slot inventory passed directly to model-free discover_scope. Candidate recall@5 is macro recall against all eligible slots for positive/ambiguous queries; unknown queries are excluded. Unique-target recall is separate. Wrong binding means any returned scope for an ambiguous/unknown case, or a scope outside gold. No LLM binding stage is simulated.",
                  "- Hybrid 100k is skipped if 10k measured document embedding time x10 exceeds --hybrid-100k-embedding-limit-s (default 300 seconds); this is an embedding-time projection, not a measured 100k result.",
              ], "reproduce": "python -B scripts/bench_scale.py"}
    def save():
        # This directory was created exclusively by this run.
        (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.output / "results.md").write_text(render(report), encoding="utf-8")
    save()
    hybrid_10k = None
    jobs = [(mode, size) for size in sorted(set(args.sizes)) for mode in ("bm25", "hybrid")]
    jobs += [("temporal", 100_000), *(("routing", n) for n in (4, 100, 1000))]
    for mode, size in jobs:
        if mode == "hybrid" and size == 100_000 and hybrid_10k:
            projected = hybrid_10k["document_embedding_s"] * 10
            if projected > args.hybrid_100k_embedding_limit_s:
                report["rows"].append({"component": mode, "size": size, "status": "skipped",
                    "reason": f"10k document embeddings took {hybrid_10k['document_embedding_s']:.2f}s; "
                              f"linear 100k projection {projected:.2f}s exceeds configured "
                              f"{args.hybrid_100k_embedding_limit_s:.2f}s limit.",
                    "projected_embedding_s": projected})
                save()
                continue
        print(f"Measuring {mode} {size:,} ...", flush=True)
        start = time.perf_counter()
        result = subprocess.run(worker_command(args, mode, size), cwd=ROOT,
                                stdout=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{mode} {size} worker failed ({result.returncode}); completed rows saved at {args.output}")
        data = json.loads(result.stdout)
        rows = data if isinstance(data, list) else [data]
        report["rows"].extend(rows)
        if mode == "hybrid" and size == 10_000:
            hybrid_10k = data
        save()
        print(f"Finished {mode} {size:,} in {time.perf_counter() - start:.1f}s", flush=True)
    report["finished_utc"] = datetime.now(UTC).isoformat()
    save()
    print(render(report))
    print(f"Results: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
