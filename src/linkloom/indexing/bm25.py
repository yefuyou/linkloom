"""Lifecycle-aware BM25 inverted index backed by rank-bm25."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from rank_bm25 import BM25Okapi

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


@dataclass(slots=True)
class _WorkspaceIndex:
    documents: list[IndexDocument]
    tokens: list[list[str]]
    engine: BM25Okapi
    inverted: dict[str, set[str]]


class BM25Index:
    """Explicit mutable BM25 component; index construction occurs on lifecycle updates."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], IndexDocument] = {}
        self._workspaces: dict[str, _WorkspaceIndex] = {}
        self._scopes: dict[tuple[str, str], _WorkspaceIndex] = {}

    def build(self, documents: Iterable[IndexDocument]) -> None:
        self._documents = {
            (document.workspace_id, document.resource_id): document
            for document in documents
        }
        self._rebuild_all()

    def upsert(self, document: IndexDocument) -> None:
        self._documents[(document.workspace_id, document.resource_id)] = document
        self._rebuild_workspace(document.workspace_id)

    def remove(self, workspace_id: str, resource_id: str) -> None:
        self._documents.pop((workspace_id, resource_id), None)
        self._rebuild_workspace(workspace_id)

    def move(self, workspace_id: str, resource_id: str, new_path: str) -> None:
        key = (workspace_id, resource_id)
        document = self._documents.get(key)
        if document is None:
            raise KeyError(f"unknown lexical-index resource: {workspace_id}/{resource_id}")
        self._documents[key] = replace(document, logical_path=normalize_logical_path(new_path))
        self._rebuild_workspace(workspace_id)

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

        scores = workspace.engine.get_scores(query_tokens)
        ranked: list[IndexSearchResult] = []
        for position, document in enumerate(workspace.documents):
            if document.resource_id not in candidate_ids:
                continue
            ranked.append(
                IndexSearchResult(
                    workspace_id=document.workspace_id,
                    resource_id=document.resource_id,
                    evidence_id=document.evidence_id,
                    logical_path=document.logical_path,
                    source_ref=document.source_ref,
                    score=float(scores[position]),
                    metadata=dict(document.metadata),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.logical_path, item.evidence_id))
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

    def _rebuild_all(self) -> None:
        self._workspaces.clear()
        self._scopes.clear()
        for workspace_id in sorted({key[0] for key in self._documents}):
            self._rebuild_workspace(workspace_id)

    def _rebuild_workspace(self, workspace_id: str) -> None:
        self._scopes = {
            key: index
            for key, index in self._scopes.items()
            if key[0] != workspace_id
        }
        documents = sorted(
            [document for (scope, _), document in self._documents.items() if scope == workspace_id],
            key=lambda item: (item.logical_path, item.resource_id),
        )
        if not documents:
            self._workspaces.pop(workspace_id, None)
            return
        self._workspaces[workspace_id] = self._build_index(documents)

        scoped_documents: defaultdict[str, list[IndexDocument]] = defaultdict(list)
        for document in documents:
            current: str | None = document.logical_path
            while current is not None:
                scoped_documents[current].append(document)
                current = parent_logical_path(current)
        for scope_path, scope_documents in scoped_documents.items():
            self._scopes[(workspace_id, scope_path)] = self._build_index(scope_documents)

    @staticmethod
    def _build_index(documents: list[IndexDocument]) -> _WorkspaceIndex:
        tokens = [tokenize_for_index(document.content) for document in documents]
        inverted: defaultdict[str, set[str]] = defaultdict(set)
        for document, document_tokens in zip(documents, tokens, strict=True):
            for token in set(document_tokens):
                inverted[token].add(document.resource_id)
        return _WorkspaceIndex(
            documents=documents,
            tokens=tokens,
            engine=BM25Okapi(tokens),
            inverted=dict(inverted),
        )
