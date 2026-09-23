"""Retrieval V2 public contracts and implementations."""

from .metrics import (
    RetrievalCaseEvaluation,
    RetrievalCaseResult,
    RetrievalEvaluationReport,
    evaluate_retrieval,
    retrieval_case_metrics,
)
from .benchmark import (
    FrozenRetrievalBenchmark,
    FrozenRetrievalCase,
    RetrievalBenchmarkReport,
    write_benchmark_report,
)
from .models import RetrievedEvidence, RetrievalQuery
from .retrievers import (
    BM25Retriever,
    CurrentRetriever,
    DenseRetriever,
    DirectoryAwareHybridRetriever,
    HybridRetriever,
    ProgressiveContextLoader,
    Retriever,
)

__all__ = [
    "BM25Retriever",
    "CurrentRetriever",
    "DenseRetriever",
    "DirectoryAwareHybridRetriever",
    "HybridRetriever",
    "FrozenRetrievalBenchmark",
    "FrozenRetrievalCase",
    "ProgressiveContextLoader",
    "RetrievedEvidence",
    "RetrievalCaseEvaluation",
    "RetrievalCaseResult",
    "RetrievalBenchmarkReport",
    "RetrievalEvaluationReport",
    "RetrievalQuery",
    "Retriever",
    "evaluate_retrieval",
    "retrieval_case_metrics",
    "write_benchmark_report",
]
