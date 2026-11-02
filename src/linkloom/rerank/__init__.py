"""Optional reranking APIs; importing this package loads no model runtime."""
from importlib import import_module
from .base import NoopReranker, RerankCandidate, RerankError, Reranker
from .pipeline import RerankPipeline, build_reranker_from_env

__all__ = ["Reranker", "RerankCandidate", "RerankError", "NoopReranker", "RerankPipeline", "LocalCrossEncoder", "JevReranker", "build_reranker_from_env"]


def __getattr__(name: str):
    if name in {"LocalCrossEncoder", "JevReranker"}:
        module = "local" if name == "LocalCrossEncoder" else "jev"
        return getattr(import_module(f".{module}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
