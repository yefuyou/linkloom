from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from linkloom.indexing.bm25 import BM25Index
from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
from linkloom.retrieval_v2.shared_lexical import WorkspaceLexicalIndexes
from tests.unit.test_agent_retrieval_v2_integration import _note, TermEmbedder
from tests.test_bm25_incremental import assert_equivalent
from tests.bm25_benchmark import corpus


def backend(owner, notes, workspace='w', mode='bm25'):
    return RuntimeRetrievalBackend(workspace_id=workspace, document_provider=lambda: notes,
        mode=mode, embedder=TermEmbedder(), lexical_indexes=owner)


def test_fresh_runtimes_reuse_update_delete_and_restore_snapshot():
    owner = WorkspaceLexicalIndexes()
    notes = [_note('d/a.md', 'supplier alpha'), _note('d/b.md', 'supplier beta')]
    original = list(notes)
    with patch.object(BM25Index, 'build', autospec=True, side_effect=BM25Index.build) as build:
        first = backend(owner, notes)
        expected = first.search('supplier', top_k=10).evidence
        for _ in range(5):
            fresh = backend(owner, notes)
            assert fresh.search('supplier', top_k=10).evidence == expected
        assert build.call_count == 1
        notes[0] = _note('d/a.md', 'supplier changed alpha')
        notes.pop()
        updated = backend(owner, notes)
        actual = updated.search('supplier', top_k=10).evidence
        isolated = backend(None, notes).search('supplier', top_k=10).evidence
        assert actual == isolated
        assert build.call_count == 2  # one shared + isolated oracle
        assert first.search('supplier', top_k=10).evidence == actual
        # An earlier authorized source snapshot is allowed to resume.
        restored = backend(owner, original)
        assert restored.search('supplier', top_k=10).evidence == expected
        assert build.call_count == 2
        assert backend(owner, [], 'other').search('supplier', top_k=10).evidence == []
        assert restored.search('supplier', top_k=10).evidence == expected
        owner.clear()
        backend(owner, original).search('supplier', top_k=10)
        assert build.call_count == 4


def test_concurrent_snapshots_and_workspace_isolation():
    owner = WorkspaceLexicalIndexes()
    versions = [[_note('d/a.md', f'supplier version{i}'), _note(f'd/{i}.md', 'supplier rollout')]
                for i in range(8)]
    expected = [backend(None, notes).search('supplier', top_k=10).evidence for notes in versions]
    def ask(i):
        b = backend(owner, versions[i])
        for _ in range(4):
            assert b.search('supplier', top_k=10).evidence == expected[i]
        return b._lexical_index
    with ThreadPoolExecutor(max_workers=8) as executor:
        indexes = list(executor.map(ask, range(8)))
    assert len({id(index) for index in indexes}) == 1
    foreign = backend(owner, [_note('foreign.md', 'supplier')], 'foreign')
    assert foreign.search('supplier', top_k=10).evidence[0]['relative_path'] == 'foreign.md'
    assert backend(owner, versions[0]).search('supplier', top_k=10).evidence == expected[0]


def test_direct_bm25_concurrent_updates_and_queries():
    index = BM25Index()
    docs = corpus(100)
    index.build(docs)
    def update():
        for i in range(30):
            index.upsert(replace(docs[0], content='alpha changed ' * (i + 1)))
            index.move('w', '1', '/else/1.md' if i % 2 else '/d1/1.md')
    def query():
        for _ in range(30):
            hits = index.search('alpha', workspace_id='w', top_k=100)
            assert len(hits) == 100
            assert len({hit.resource_id for hit in hits}) == 100
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(update)] + [executor.submit(query) for _ in range(3)]
        for future in futures:
            future.result()
    final = [index.get_document('w', str(i)) for i in range(100)]
    assert_equivalent(index, final)

def test_product_asks_share_index_across_fresh_engines(tmp_path, monkeypatch):
    import json
    from time import monotonic, sleep
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
    from linkloom.tools.contracts import ToolCall

    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE', 'bm25')
    app = ProductApplication(database_path=tmp_path / 'synthetic.sqlite', extractor=None)
    workspace = app.repository.create_workspace('Synthetic')
    source = dict(source_id='src_' + 'a' * 32, source_version='v1', name='synthetic.md',
                  content='Supplier alpha is approved.')
    monkeypatch.setattr(app, 'snapshot_investigation_sources', lambda _id: (workspace, (source.copy(),)))
    seen = []
    def search(request):
        context = json.loads(request.user_input.rsplit('team_decision_request=', 1)[1])['source_context']
        return ModelAction.tool(ToolCall(call_id='synthetic-search', tool_id='search_notes',
            arguments=dict(query='supplier', source_context=context, limit=5)))
    def finish(request):
        seen.append(request.observation.value)
        return ModelAction.final(json.dumps({
            'schema_version': 'team-decision-result/v1', 'decision': None, 'rationale': [],
            'rejected_alternatives': [], 'actions': [], 'unresolved_items': [],
            'uncertainty': {'status': 'insufficient_evidence', 'statement': 'Synthetic search only',
                            'unknown_fields': ['decision'], 'evidence_refs': []}, 'evidence_refs': []}))
    service = ProductInvestigationService(app, model_factory=lambda _: FakeModelAdapter([search, finish]),
                                           external_provider=False, max_steps=3)
    try:
        with patch.object(BM25Index, 'build', autospec=True, side_effect=BM25Index.build) as build:
            for i in range(3):
                started = service.start('Find supplier evidence', workspace['workspace_id'])
                snapshot = started
                deadline = monotonic() + 15
                while snapshot['kind'] == 'running' and monotonic() < deadline:
                    sleep(0.01)
                    snapshot = service.inspect(started['run']['id'])
                assert snapshot['kind'] != 'running'
                assert len(seen) == i + 1, snapshot
                assert seen[-1], snapshot
            assert build.call_count == 1
            engines = [job.backend.engine for job in service._jobs.values()]
            assert len({id(engine) for engine in engines}) == 3
            assert all(engine._lexical_indexes is service._lexical_indexes for engine in engines)
            assert seen[0] == seen[1] == seen[2]
    finally:
        app.close()

def test_shared_hybrid_matches_isolated_after_snapshot_changes():
    owner = WorkspaceLexicalIndexes()
    notes = [_note('d/a.md', 'supplier rollout 项目'), _note('d/b.md', 'budget supplier')]
    for operation in range(5):
        shared = backend(owner, notes, mode='hybrid')
        isolated = backend(None, notes, mode='hybrid')
        for scope in (None, '/d', '/else'):
            assert shared.search('supplier 项目', top_k=10, scope_path=scope).evidence == isolated.search(
                'supplier 项目', top_k=10, scope_path=scope).evidence
        if operation == 0:
            notes.append(_note('d/c.md', 'supplier 项目 new'))
        elif operation == 1:
            notes[0] = _note('d/a.md', 'supplier budget changed 项目')
        elif operation == 2:
            notes.pop()
        elif operation == 3:
            notes[0] = _note('else/a.md', notes[0].content)
