from dataclasses import replace
from unittest.mock import patch

import pytest
from rank_bm25 import BM25Okapi

from linkloom.indexing.bm25 import BM25Index, tokenize_for_index
from tests.bm25_benchmark import corpus


def assert_equivalent(index, documents):
    for workspace in ('w', 'other', 'empty'):
        for scope in (None, '/', '/d0', '/d1', '/else', '/d1/renamed.md'):
            selected = sorted([d for d in documents if d.workspace_id == workspace and
                (scope is None or scope == '/' or d.logical_path == scope or d.logical_path.startswith(scope + '/'))],
                key=lambda d: (d.logical_path, d.resource_id))
            tokens = [tokenize_for_index(d.content) for d in selected]
            engine = BM25Okapi(tokens) if any(tokens) else None
            for query in ('alpha', '项目', '检索 alpha group3', 'unknown', 'beta beta'):
                terms = tokenize_for_index(query)
                scores = engine.get_scores(terms) if engine else []
                expected = [(d, float(score)) for d, ts, score in zip(selected, tokens, scores)
                            if set(ts).intersection(terms)]
                expected.sort(key=lambda item: (-item[1], item[0].logical_path, item[0].evidence_id))
                actual = index.search(query, workspace_id=workspace, scope_path=scope, top_k=1000)
                assert [(h.resource_id, h.evidence_id, h.logical_path, h.source_ref, h.metadata) for h in actual] == [
                    (d.resource_id, d.evidence_id, d.logical_path, d.source_ref, d.metadata) for d, _ in expected]
                assert [h.score for h in actual] == pytest.approx([s for _, s in expected], rel=1e-12, abs=1e-12)


def test_lifecycle_equivalence():
    documents = corpus(80)
    documents += [replace(documents[0], workspace_id='other'), replace(documents[1], resource_id='blank', content='')]
    index = BM25Index()
    index.build(documents)
    assert_equivalent(index, documents)
    for i in range(15):
        old = documents[i]
        new = replace(old, content='beta 项目 changed ' * (i + 1))
        documents[i] = new
        index.upsert(new)
        assert_equivalent(index, documents)
    for path in ('/d1/renamed.md', '/else/renamed.md'):
        documents[1] = replace(documents[1], logical_path=path)
        index.move('w', '1', path)
        assert_equivalent(index, documents)
    index.remove('w', '0')
    documents = [d for d in documents if (d.workspace_id, d.resource_id) != ('w', '0')]
    assert_equivalent(index, documents)
    new = replace(documents[0], resource_id='added')
    index.upsert(new)
    documents.append(new)
    assert_equivalent(index, documents)


def test_only_changed_content_is_tokenized():
    index = BM25Index()
    documents = corpus(1000)
    with patch('linkloom.indexing.bm25.tokenize_for_index', wraps=tokenize_for_index) as tokenize:
        index.build(documents)
        assert tokenize.call_count == 1000
        tokenize.reset_mock()
        untouched = index._scopes['w', '/d2']
        index.upsert(replace(documents[0], content='new content'))
        assert tokenize.call_count == 1
        assert index._scopes['w', '/d2'] is untouched
        tokenize.reset_mock()
        index.move('w', '1', '/else/1.md')
        index.remove('w', '2')
        index.upsert(index.get_document('w', '3'))
        assert tokenize.call_count == 0
    index.build([])
    assert index.search('alpha', workspace_id='w') == []
    index.upsert(replace(documents[0], content=''))
    assert index.search('alpha', workspace_id='w') == []


def test_runtime_recreation_and_hybrid_candidates(monkeypatch):
    from tests.unit.test_agent_retrieval_v2_integration import _note, TermEmbedder
    from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
    from linkloom.retrieval_v2.models import RetrievalQuery
    from linkloom.retrieval_v2.retrievers import BM25Retriever

    class FullRebuildIndex(BM25Index):
        def search(self, query, *, workspace_id, top_k=5, scope_path=None):
            documents = sorted([d for d in self._documents.values() if d.workspace_id == workspace_id
                and (scope_path is None or scope_path == '/' or d.logical_path == scope_path
                     or d.logical_path.startswith(scope_path + '/'))], key=lambda d: (d.logical_path, d.resource_id))
            from linkloom.indexing.models import IndexSearchResult
            terms = tokenize_for_index(query)
            tokens = [tokenize_for_index(d.content) for d in documents]
            engine = BM25Okapi(tokens)
            scores = engine.get_scores(terms)
            hits = [IndexSearchResult(d.workspace_id, d.resource_id, d.evidence_id, d.logical_path,
                    d.source_ref, float(s), dict(d.metadata)) for d, ts, s in zip(documents, tokens, scores)
                    if set(ts).intersection(terms)]
            return sorted(hits, key=lambda h: (-h.score, h.logical_path, h.evidence_id))[:top_k]

    notes = [_note(f'd{i % 2}/{i}.md', f'项目 supplier rollout budget {i}') for i in range(20)]
    backend = RuntimeRetrievalBackend(workspace_id='w', document_provider=lambda: notes,
        mode='hybrid', embedder=TermEmbedder())
    for step in range(4):
        backend.search('supplier 项目', top_k=5)
        oracle = FullRebuildIndex()
        oracle.build(backend._indexed_documents.values())
        retriever = backend._retrievers[backend.mode]
        lexical = retriever.lexical
        for scope in (None, '/d0', '/d1'):
            query = RetrievalQuery(workspace_id='w', query='supplier 项目', top_k=20, optional_scope_path=scope)
            actual = retriever.retrieve(query)
            retriever.lexical = BM25Retriever(oracle)
            expected = retriever.retrieve(query)
            retriever.lexical = lexical
            assert [(h.resource_id, h.score) for h in actual] == [(h.resource_id, h.score) for h in expected]
        notes[step] = _note(notes[step].relative_path, 'supplier changed 项目')

    builds = []
    original = BM25Index.build
    def count(self, documents):
        builds.append(1)
        original(self, documents)
    monkeypatch.setattr(BM25Index, 'build', count)
    for _ in range(2):
        fresh = RuntimeRetrievalBackend(workspace_id='w', document_provider=lambda: notes, mode='bm25')
        fresh.search('supplier', top_k=5)
        fresh.search('supplier', top_k=5)
    # Existing ownership: one build per backend, none for its repeated queries.
    assert len(builds) == 2

