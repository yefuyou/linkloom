"""Frozen 30-case retrieval benchmark with evaluator-side relevance."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from linkloom.indexing import (
    BM25Index,
    DirectoryIndex,
    EmbeddingProvider,
    IndexDocument,
    SentenceTransformerEmbedder,
    VectorIndex,
)
from linkloom.schemas import NoteDocument

from .metrics import RetrievalCaseResult, RetrievalEvaluationReport, evaluate_retrieval
from .models import RetrievalQuery
from .retrievers import (
    BM25Retriever,
    CurrentRetriever,
    DenseRetriever,
    DirectoryAwareHybridRetriever,
    HybridRetriever,
    Retriever,
)


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True, slots=True)
class FrozenRetrievalCase:
    case_id: str
    workspace_id: str
    query: str
    relevant_source_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkReport:
    modes: dict[str, RetrievalEvaluationReport]
    gold_sha256_before: str
    gold_sha256_after: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class FrozenRetrievalBenchmark:
    """Build all indexes once, then compare fixed retrievers without mutating Gold."""

    def __init__(
        self,
        repo_root: Path | str,
        *,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.seed_root = self.repo_root / "docs" / "requirements" / "m1_team_decision_eval_seed"
        self.dataset_path = self.seed_root / "dataset.jsonl"
        self.embedder = embedder or SentenceTransformerEmbedder()
        self.cases = self._load_cases()
        self.documents = self._load_documents()
        self.note_documents = self._load_note_documents()

    def run(self, *, include_directory_aware: bool = True, top_k: int = 5) -> RetrievalBenchmarkReport:
        before = _sha256_file(self.dataset_path)
        lexical_index = BM25Index()
        lexical_index.build(self.documents)
        vector_index = VectorIndex(self.embedder)
        vector_index.build(self.documents)
        directory_index = DirectoryIndex()
        directory_index.build(self.documents)
        for node in directory_index.list_nodes():
            if node.contained_documents:
                directory_index.refresh_summaries(node.workspace_id, node.path)

        current = CurrentRetriever(
            lambda workspace_id: list(self.note_documents.get(workspace_id, ()))
        )
        bm25 = BM25Retriever(lexical_index)
        dense = DenseRetriever(vector_index)
        hybrid = HybridRetriever(bm25, dense)
        retrievers: dict[str, Retriever] = {
            "current": current,
            "bm25": bm25,
            "dense": dense,
            "hybrid": hybrid,
        }
        if include_directory_aware:
            retrievers["directory_hybrid"] = DirectoryAwareHybridRetriever(
                hybrid,
                directory_index,
                enabled=True,
            )

        mode_reports = {
            mode: self._run_mode(retriever, top_k=top_k)
            for mode, retriever in retrievers.items()
        }
        after = _sha256_file(self.dataset_path)
        if before != after:
            raise RuntimeError("frozen retrieval Gold changed during benchmark")
        return RetrievalBenchmarkReport(
            modes=mode_reports,
            gold_sha256_before=before,
            gold_sha256_after=after,
        )

    def _run_mode(self, retriever: Retriever, *, top_k: int) -> RetrievalEvaluationReport:
        results: list[RetrievalCaseResult] = []
        for case in self.cases:
            started = time.perf_counter()
            hits = retriever.retrieve(
                RetrievalQuery(
                    workspace_id=case.workspace_id,
                    query=case.query,
                    top_k=top_k,
                )
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            results.append(
                RetrievalCaseResult(
                    case_id=case.case_id,
                    ranked_source_refs=tuple(hit.source_ref for hit in hits),
                    relevant_source_refs=case.relevant_source_refs,
                    latency_ms=latency_ms,
                )
            )
        return evaluate_retrieval(results)

    def _load_cases(self) -> tuple[FrozenRetrievalCase, ...]:
        cases: list[FrozenRetrievalCase] = []
        for line in self.dataset_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cases.append(
                FrozenRetrievalCase(
                    case_id=str(row["case_id"]),
                    workspace_id=str(row["workspace_id"]),
                    query=str(row["user_question"]),
                    relevant_source_refs=frozenset(str(path) for path in row["expected_relevant_notes"]),
                )
            )
        return tuple(cases)

    def _load_documents(self) -> tuple[IndexDocument, ...]:
        documents: list[IndexDocument] = []
        workspace_root = self.seed_root / "workspaces"
        for path in sorted(workspace_root.glob("*/*.md")):
            workspace_id = path.parent.name
            source_ref = path.relative_to(self.seed_root).as_posix()
            content = path.read_text(encoding="utf-8")
            documents.append(
                IndexDocument(
                    workspace_id=workspace_id,
                    resource_id=source_ref,
                    evidence_id=f"document:{source_ref}",
                    logical_path=f"/{source_ref}",
                    content=f"{path.stem}\n{content}",
                    source_ref=source_ref,
                    metadata={
                        "title": path.stem,
                        "content": content,
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
            )
        return tuple(documents)

    def _load_note_documents(self) -> dict[str, tuple[NoteDocument, ...]]:
        grouped: dict[str, list[NoteDocument]] = {}
        for document in self.documents:
            content = str(document.metadata["content"])
            headings = []
            for line_number, line in enumerate(content.splitlines(), start=1):
                match = _HEADING.match(line)
                if match:
                    headings.append(
                        {
                            "level": len(match.group(1)),
                            "text": match.group(2).strip(),
                            "line": line_number,
                        }
                    )
            grouped.setdefault(document.workspace_id, []).append(
                NoteDocument(
                    relative_path=document.source_ref,
                    title=PurePosixPath(document.source_ref).stem,
                    content=content,
                    headings=headings,
                    tags=[],
                    wikilinks=[],
                    size_bytes=len(content.encode("utf-8")),
                    content_sha256=str(document.metadata["content_sha256"]),
                    line_count=len(content.splitlines()),
                )
            )
        return {
            workspace_id: tuple(sorted(notes, key=lambda note: note.relative_path))
            for workspace_id, notes in grouped.items()
        }


def write_benchmark_report(report: RetrievalBenchmarkReport, output_path: Path | str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_ready(item) for item in value)
    return value
