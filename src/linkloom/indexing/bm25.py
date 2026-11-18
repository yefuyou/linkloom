"""Incremental BM25 inverted index using rank-bm25 Okapi scoring."""

from __future__ import annotations

import math
import re
from functools import wraps
from threading import RLock
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import numpy as np

from linkloom.context import normalize_logical_path, parent_logical_path
from linkloom.indexing.models import IndexDocument, IndexSearchResult


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", re.UNICODE)


def tokenize_for_index(text: str) -> list[str]:
    """Tokenize Latin terms and add deterministic CJK bigrams for exact matching."""
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text.casefold()):
        segment = match.group(0)
        tokens.append(segment)
        if segment and all("\u4e00" <= char <= "\u9fff" for char in segment):
            tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return tokens


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


@dataclass(slots=True)
class _WorkspaceIndex:
    documents: dict[str, IndexDocument] = field(default_factory=dict)
    frequencies: dict[str, Counter[str]] = field(default_factory=dict)
    lengths: dict[str, int] = field(default_factory=dict)
    inverted: dict[str, set[str]] = field(default_factory=dict)
    total_length: int = 0
    idf: dict[str, float] | None = None

    def remove(self, resource_id: str) -> None:
        if resource_id not in self.documents:
            return
        self.documents.pop(resource_id)
        self.total_length -= self.lengths.pop(resource_id)
        for token in self.frequencies.pop(resource_id):
            posting = self.inverted[token]
            posting.remove(resource_id)
            if not posting:
                del self.inverted[token]
        self.idf = None

    def put(self, document: IndexDocument, frequencies: Counter[str], length: int) -> None:
        resource_id = document.resource_id
        if self.frequencies.get(resource_id) == frequencies:
            self.documents[resource_id] = document
            return
        self.remove(resource_id)
        self.documents[resource_id] = document
        self.frequencies[resource_id] = frequencies
        self.lengths[resource_id] = length
        self.total_length += length
        for token in frequencies:
            self.inverted.setdefault(token, set()).add(resource_id)
        self.idf = None

    def refresh_idf(self) -> None:
        n = len(self.documents)
        self.idf = {
            token: math.log(n - len(posting) + 0.5) - math.log(len(posting) + 0.5)
            for token, posting in self.inverted.items()
        }
        average = math.fsum(self.idf.values()) / len(self.idf) if self.idf else 0.0
        self.idf = {token: value if value >= 0 else 0.25 * average
                    for token, value in self.idf.items()}

    def scores(self, query: list[str], ids: list[str]) -> np.ndarray:
        # rank-bm25 0.2.2 Okapi defaults, including its negative-IDF floor.
        # N changes every IDF, so refresh the vocabulary lazily, never postings.
        if self.idf is None:
            self.refresh_idf()
        lengths = np.array([self.lengths[rid] for rid in ids])
        avgdl = self.total_length / len(self.documents)
        scores = np.zeros(len(ids))
        frequencies = [self.frequencies[rid] for rid in ids]
        for token in query:
            frequency = np.array([terms.get(token, 0) for terms in frequencies])
            scores += (self.idf.get(token) or 0) * (
                frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * lengths / avgdl))
            )
        return scores


class BM25Index:
    """Explicit mutable BM25 component; index construction occurs on lifecycle updates."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._documents: dict[tuple[str, str], IndexDocument] = {}
        self._workspaces: dict[str, _WorkspaceIndex] = {}
        self._terms: dict[tuple[str, str], tuple[Counter[str], int]] = {}
        self._scopes: dict[tuple[str, str], _WorkspaceIndex] = {}

    @_locked
    def build(self, documents: Iterable[IndexDocument]) -> None:
        self._documents.clear()
        self._terms.clear()
        self._workspaces.clear()
        self._scopes.clear()
        for document in documents:
            self.upsert(document)

    @staticmethod
    def _paths(document: IndexDocument) -> set[str]:
        paths: set[str] = set()
        current: str | None = document.logical_path
        while current is not None:
            paths.add(current)
            current = parent_logical_path(current)
        return paths

    @_locked
    def upsert(self, document: IndexDocument) -> None:
        key = (document.workspace_id, document.resource_id)
        old = self._documents.get(key)
        if old == document:
            return
        if old is None or old.content != document.content:
            tokens = tokenize_for_index(document.content)
            self._terms[key] = (Counter(tokens), len(tokens))
        frequencies, length = self._terms[key]
        old_paths = self._paths(old) if old else set()
        paths = self._paths(document)
        for path in old_paths - paths:
            scope_key = (document.workspace_id, path)
            index = self._scopes[scope_key]
            index.remove(document.resource_id)
            if not index.documents:
                del self._scopes[scope_key]
        self._documents[key] = document
        self._workspaces.setdefault(document.workspace_id, _WorkspaceIndex()).put(document, frequencies, length)
        for path in paths:
            self._scopes.setdefault((document.workspace_id, path), _WorkspaceIndex()).put(document, frequencies, length)

    @_locked
    def remove(self, workspace_id: str, resource_id: str) -> None:
        document = self._documents.pop((workspace_id, resource_id), None)
        if document is None:
            return
        self._terms.pop((workspace_id, resource_id))
        index = self._workspaces[workspace_id]
        index.remove(resource_id)
        if not index.documents:
            del self._workspaces[workspace_id]
        for path in self._paths(document):
            key = (workspace_id, path)
            index = self._scopes[key]
            index.remove(resource_id)
            if not index.documents:
                del self._scopes[key]

    @_locked
    def move(self, workspace_id: str, resource_id: str, new_path: str) -> None:
        document = self._documents.get((workspace_id, resource_id))
        if document is None:
            raise KeyError(f"unknown lexical-index resource: {workspace_id}/{resource_id}")
        self.upsert(replace(document, logical_path=normalize_logical_path(new_path)))

    @_locked
    def search(
        self,
        query: str,
        *,
        workspace_id: str,
        top_k: int = 5,
        scope_path: str | None = None,
    ) -> list[IndexSearchResult]:
        if top_k <= 0:
            return []
        normalized_scope = normalize_logical_path(scope_path) if scope_path else None
        workspace = (
            self._scopes.get((workspace_id, normalized_scope))
            if normalized_scope is not None
            else self._workspaces.get(workspace_id)
        )
        query_tokens = tokenize_for_index(query)
        if workspace is None or not query_tokens:
            return []

        candidate_ids: set[str] = set()
        for token in query_tokens:
            candidate_ids.update(workspace.inverted.get(token, ()))
        if not candidate_ids:
            return []

        ids = sorted(candidate_ids)
        scores = workspace.scores(query_tokens, ids)
        # Materialize only the requested evidence, not every matching document.
        # resource_id preserves the old stable order for identical path/evidence.
        ranked = [(float(scores[position]), workspace.documents[rid])
                  for position, rid in enumerate(ids)]
        ranked.sort(key=lambda item: (-item[0], item[1].logical_path,
                                      item[1].evidence_id, item[1].resource_id))
        return [IndexSearchResult(
            workspace_id=document.workspace_id,
            resource_id=document.resource_id,
            evidence_id=document.evidence_id,
            logical_path=document.logical_path,
            source_ref=document.source_ref,
            score=score,
            metadata=dict(document.metadata),
        ) for score, document in ranked[:top_k]]

    @_locked
    def resource_ids(self, workspace_id: str | None = None) -> set[str]:
        return {
            resource_id
            for (document_workspace, resource_id) in self._documents
            if workspace_id is None or document_workspace == workspace_id
        }

    @_locked
    def workspace_ids(self) -> set[str]:
        return {workspace_id for workspace_id, _ in self._documents}

    @_locked
    def get_document(self, workspace_id: str, resource_id: str) -> IndexDocument | None:
        return self._documents.get((workspace_id, resource_id))
