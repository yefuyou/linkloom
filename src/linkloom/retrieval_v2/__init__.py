"""Retrieval V2 product contracts with opt-in research exports."""

from importlib import import_module

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
from .runtime_backend import (
    RetrievalMode,
    RetrievalObservation,
    RuntimeRetrievalBackend,
    RuntimeRetrievalResult,
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
    "RetrievalMode",
    "RetrievalObservation",
    "RuntimeRetrievalBackend",
    "RuntimeRetrievalResult",
    "evaluate_retrieval",
    "retrieval_case_metrics",
    "write_benchmark_report",
]

# Keep historical research APIs available without loading them into the Runtime.
_RESEARCH_EXPORTS = {
    "RetrievalCaseEvaluation": "metrics",
    "RetrievalCaseResult": "metrics",
    "RetrievalEvaluationReport": "metrics",
    "evaluate_retrieval": "metrics",
    "retrieval_case_metrics": "metrics",
    "FrozenRetrievalBenchmark": "benchmark",
    "FrozenRetrievalCase": "benchmark",
    "RetrievalBenchmarkReport": "benchmark",
    "write_benchmark_report": "benchmark",
}


def __getattr__(name: str):
    module = _RESEARCH_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
