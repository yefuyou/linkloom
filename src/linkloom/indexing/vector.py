"""Replaceable dense vector index using exact cosine search for the frozen dataset."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
import time
from typing import Any, Protocol

import numpy as np

from linkloom.context import normalize_logical_path
from linkloom.indexing.models import IndexDocument, IndexSearchResult


class EmbeddingProvider(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Lazy local embedder; no paid or remote provider calls are made."""

    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") -> None:
        self.model_name = model_name
        self._model: Any | None = None
        self.model_load_total_ms = 0.0
        self.last_model_load_ms = 0.0

    def encode(self, texts: list[str]) -> np.ndarray:
        self.last_model_load_ms = 0.0
        if self._model is None:
            started = time.perf_counter()
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self.last_model_load_ms = (time.perf_counter() - started) * 1000.0
            self.model_load_total_ms += self.last_model_load_ms
        return np.asarray(
            self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=False),
            dtype=np.float32,
        )


class VectorIndex:
    """Mutable exact-cosine vector index behind a replaceable component boundary."""

    def __init__(self, embedder: EmbeddingProvider) -> None:
        self._embedder = embedder
        self._documents: dict[tuple[str, str], IndexDocument] = {}
        self._vectors: dict[tuple[str, str], np.ndarray] = {}
        self.last_model_load_ms = 0.0
        self.last_document_embedding_ms = 0.0
        self.last_query_embedding_ms = 0.0
        self.last_query_model_load_ms = 0.0
        self.last_dense_search_ms = 0.0
        self.last_normalization_ms = 0.0

    def build(self, documents: Iterable[IndexDocument]) -> None:
        self.last_model_load_ms = 0.0
        self.last_document_embedding_ms = 0.0
        self.last_normalization_ms = 0.0
        document_list = list(documents)
        self._documents.clear()
        self._vectors.clear()
        if not document_list:
            return
        vectors = self._encode(
            (document.content for document in document_list),
            purpose="document",
        )
        for document, vector in zip(document_list, vectors, strict=True):
            key = (document.workspace_id, document.resource_id)
            self._documents[key] = document
            self._vectors[key] = vector

    def upsert(self, document: IndexDocument) -> None:
        self.last_model_load_ms = 0.0
        self.last_document_embedding_ms = 0.0
        self.last_normalization_ms = 0.0
        vector = self._encode([document.content], purpose="document")[0]
        key = (document.workspace_id, document.resource_id)
        self._documents[key] = document
        self._vectors[key] = vector

    def remove(self, workspace_id: str, resource_id: str) -> None:
        key = (workspace_id, resource_id)
        self._documents.pop(key, None)
        self._vectors.pop(key, None)

    def move(self, workspace_id: str, resource_id: str, new_path: str) -> None:
        key = (workspace_id, resource_id)
        document = self._documents.get(key)
        if document is None:
            raise KeyError(f"unknown vector-index resource: {workspace_id}/{resource_id}")
        self._documents[key] = replace(document, logical_path=normalize_logical_path(new_path))

    def search(
        self,
        query: str,
        *,
        workspace_id: str,
        top_k: int = 5,
        scope_path: str | None = None,
    ) -> list[IndexSearchResult]:
        self.last_query_embedding_ms = 0.0
        self.last_query_model_load_ms = 0.0
        self.last_dense_search_ms = 0.0
        self.last_normalization_ms = 0.0
        search_started = time.perf_counter()
        if top_k <= 0 or not query.strip():
            return []
        normalized_scope = normalize_logical_path(scope_path) if scope_path else None
        candidates = [
            (key, document)
            for key, document in self._documents.items()
            if key[0] == workspace_id
            and (normalized_scope is None or _path_is_under(document.logical_path, normalized_scope))
        ]
        if not candidates:
            self.last_dense_search_ms = (time.perf_counter() - search_started) * 1000.0
            return []
        query_vector = self._encode([query], purpose="query")[0]
        if not np.any(query_vector):
            self.last_dense_search_ms = max(
                0.0,
                (time.perf_counter() - search_started) * 1000.0
                - self.last_query_embedding_ms
                - self.last_query_model_load_ms,
            )
            return []

        ranked: list[IndexSearchResult] = []
        for key, document in candidates:
            vector = self._vectors[key]
            score = float(np.dot(query_vector, vector))
            if score <= 0.0:
                continue
            ranked.append(
                IndexSearchResult(
                    workspace_id=document.workspace_id,
                    resource_id=document.resource_id,
                    evidence_id=document.evidence_id,
                    logical_path=document.logical_path,
                    source_ref=document.source_ref,
                    score=score,
                    metadata=dict(document.metadata),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.logical_path, item.evidence_id))
        self.last_dense_search_ms = max(
            0.0,
            (time.perf_counter() - search_started) * 1000.0
            - self.last_query_embedding_ms
            - self.last_query_model_load_ms,
        )
        return ranked[:top_k]

    def resource_ids(self, workspace_id: str | None = None) -> set[str]:
        return {
            resource_id
            for (document_workspace, resource_id) in self._documents
            if workspace_id is None or document_workspace == workspace_id
        }

    def workspace_ids(self) -> set[str]:
        return {workspace_id for workspace_id, _ in self._documents}

    def get_document(self, workspace_id: str, resource_id: str) -> IndexDocument | None:
        return self._documents.get((workspace_id, resource_id))

    def _encode(self, texts: Iterable[str], *, purpose: str) -> np.ndarray:
        before_load_ms = float(getattr(self._embedder, "model_load_total_ms", 0.0))
        started = time.perf_counter()
        matrix = np.asarray(self._embedder.encode(list(texts)), dtype=np.float32)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        after_load_ms = float(getattr(self._embedder, "model_load_total_ms", before_load_ms))
        model_load_ms = max(
            0.0,
            float(
                getattr(
                    self._embedder,
                    "last_model_load_ms",
                    after_load_ms - before_load_ms,
                )
            ),
        )
        encode_ms = max(0.0, elapsed_ms - model_load_ms)
        if purpose == "document":
            self.last_model_load_ms += model_load_ms
            self.last_document_embedding_ms += encode_ms
        else:
            self.last_query_model_load_ms = model_load_ms
            self.last_query_embedding_ms = encode_ms
        if matrix.ndim != 2:
            raise ValueError("embedding provider must return a 2D matrix")
        normalization_started = time.perf_counter()
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        normalized = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms != 0)
        self.last_normalization_ms += (
            time.perf_counter() - normalization_started
        ) * 1000.0
        return normalized


def _path_is_under(path: str, scope: str) -> bool:
    return path == scope or path.startswith(f"{scope.rstrip('/')}/")
