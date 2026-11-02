"""Local model boundaries tested without downloading or loading a model."""
from __future__ import annotations
from dataclasses import replace
import json
import pytest
from linkloom.rerank.base import RerankCandidate, RerankError
from linkloom.rerank.local import LocalCrossEncoder, prepare_texts
from linkloom.rerank.model_cache import sha256, valid_artifact
from linkloom.retrieval_v2.models import RetrievedEvidence


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, tokens, skip_special_tokens=True):
        return ''.join(chr(c) for c in tokens)

    def num_special_tokens_to_add(self, pair=True):
        return 4

    def __call__(self, query, text, truncation=False):
        return {'input_ids': [0] + self.encode(query) + [1, 1] + self.encode(text) + [2]}


def candidate(number=1):
    return RerankCandidate(RetrievedEvidence(f'e{number}',f'd{number}',f'/d{number}',0.02,number,'hybrid',f'd{number}',{'content_sha256':'v1'}),'原始中文证据')


class Connection:
    def __init__(self, message=None, ready=True, broken=False):
        self.message, self.ready, self.broken = message, ready, broken
        self.sent, self.waits, self.closed = [], [], False

    def poll(self, timeout):
        self.waits.append(timeout)
        return self.ready

    def recv(self):
        if self.broken:
            raise EOFError
        return self.message

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


class Process:
    def __init__(self):
        self.alive = True
        self.terminated = False

    def join(self, timeout):
        pass

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated, self.alive = True, False

    def kill(self):
        self.alive = False


def configured_worker(connection):
    local = LocalCrossEncoder(device='cpu')
    local._connection, local._process, local._ready = connection, Process(), True
    return local


def test_real_token_budget_bounds_both_long_query_and_chinese_fragment():
    query = '中文问题' * 100
    original = '本地检索不会外发资料。' * 100
    bounded, texts, detail = prepare_texts(query, [original], CharacterTokenizer())
    assert len(bounded) == 64
    assert len(CharacterTokenizer()(bounded, texts[0])['input_ids']) <= 256
    assert detail[0]['truncated'] and detail[0]['original_tokens'] == len(original)
    assert original == '本地检索不会外发资料。' * 100


def test_short_passage_is_preserved_and_missing_content_is_rejected():
    query, fragments, detail = prepare_texts('why', ['source evidence'], CharacterTokenizer())
    assert fragments == ['source evidence'] and not detail[0]['truncated']
    with pytest.raises(RerankError, match='missing_text'):
        prepare_texts(query, [' '], CharacterTokenizer())


def test_timeout_uses_configured_deadline_and_reaps_worker():
    connection = Connection(ready=False)
    local = configured_worker(connection)
    process = local._process
    with pytest.raises(RerankError, match='inference_timeout'):
        local.rerank('query', [candidate()])
    assert len(connection.waits) == 1 and 0 < connection.waits[0] <= 5.0
    assert process.terminated and not process.is_alive()
    assert connection.closed
    assert local._process is None


def test_native_worker_exit_is_a_recoverable_failure():
    local = configured_worker(Connection(broken=True))
    with pytest.raises(RerankError, match='worker_exited'):
        local.rerank('query', [candidate()])
    assert local._process is None


def test_worker_scores_are_fresh_copies_and_original_rrf_score_is_retained():
    local = configured_worker(Connection({'kind':'scores','scores':[0.1,0.9],'input_details':[{'pair_tokens':10,'truncated':False},{'pair_tokens':12,'truncated':False}]}))
    originals = [candidate(1), candidate(2)]
    try:
        scored = local.rerank('query', originals)
        assert [x.evidence_id for x in scored] == ['e2','e1']
        assert scored[0].rerank_score == 0.9
        assert all(x.score == 0.02 for x in scored)
        assert [x.evidence.rank for x in originals] == [1,2]
        assert originals[0].evidence.rerank_score is None
        assert local.last_trace['token_counts'] == [10,12]
    finally:
        local.close()


def test_invalid_count_does_not_accept_partial_worker_results():
    local = configured_worker(Connection({'kind':'scores','scores':[], 'input_details':[]}))
    try:
        with pytest.raises(RerankError, match='invalid_scores'):
            local.rerank('query', [candidate()])
    finally:
        local.close()


def test_unprepared_first_request_does_not_block_for_model_download(monkeypatch):
    local = LocalCrossEncoder()
    def start():
        local._connection, local._process = Connection(ready=False), Process()
        import time
        local._started = time.monotonic()
    monkeypatch.setattr(local, '_start', start)
    try:
        with pytest.raises(RerankError, match='model_preparing'):
            local.rerank('query', [candidate()])
        assert local._connection.waits == [0.0]
    finally:
        local.close()


def test_cache_accepts_complete_hash_and_rejects_changed_bytes_or_identity(tmp_path):
    target = tmp_path/'model-int8.onnx'
    target.write_bytes(b'verified artifact')
    identity = {'revision':'fixed','settings':{'reduce_range':True}}
    (tmp_path/'manifest.json').write_text(json.dumps({'identity':identity,'bytes':target.stat().st_size,'sha256':sha256(target)}))
    assert valid_artifact(tmp_path, identity)
    assert not valid_artifact(tmp_path, {'revision':'different'})
    target.write_bytes(b'corrupted artifact')
    assert not valid_artifact(tmp_path, identity)


def test_concurrent_lock_wait_is_bounded_without_killing_active_request():
    import threading
    local = configured_worker(Connection(ready=False))
    local.timeout_s = .02
    held,release = threading.Event(),threading.Event()
    def owner():
        with local._lock:
            held.set()
            release.wait(2)
    thread=threading.Thread(target=owner);thread.start();assert held.wait(1)
    try:
        with pytest.raises(RerankError,match='inference_timeout'):
            local.rerank('query',[candidate()])
        assert not release.is_set()
        assert local._process.is_alive()
        assert local._connection.sent==[]
    finally:
        release.set();thread.join();local.close()


def test_jev_tokenizer_cold_start_is_nonblocking(monkeypatch):
    import threading
    import linkloom.rerank.local as module
    import linkloom.rerank.model_cache as cache
    release=threading.Event()
    def delayed():
        release.wait(2)
        return CharacterTokenizer()
    monkeypatch.setattr(cache,'load_tokenizer',delayed)
    monkeypatch.setattr(module,'_JEV_TOKENIZER',None)
    monkeypatch.setattr(module,'_JEV_PREPARE_THREAD',None)
    monkeypatch.setattr(module,'_JEV_PREPARE_FAILURE','')
    try:
        with pytest.raises(RerankError,match='model_preparing'):
            module.prepare_jev_inputs('query',[candidate()])
        assert not release.is_set()
    finally:
        release.set();module._JEV_PREPARE_THREAD.join(1)


def test_missing_cleanup_dependency_does_not_start_an_unreapable_worker(monkeypatch):
    import linkloom.rerank.local as module
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda package:None if package=='psutil' else object())
    local=LocalCrossEncoder()
    with pytest.raises(RerankError,match='missing_dependency'):
        local.rerank('query',[candidate()])
    assert local._process is None


def test_optional_metadata_copy_failure_cannot_fail_retrieval():
    from linkloom.retrieval_v2.retrievers import HybridRetriever
    from linkloom.retrieval_v2.models import RetrievalQuery
    class NotCopyable:
        def __deepcopy__(self,memo):raise RuntimeError('copy unsupported')
    class Static:
        def retrieve(self,query):return [replace(candidate().evidence,metadata={'content':'source','opaque':NotCopyable()})]
    class Forbidden:
        enabled=True
        def rerank(self,*args):pytest.fail('incomplete text batch must not reach model')
    retriever=HybridRetriever(Static(),Static(),reranker=Forbidden())
    result= retriever.retrieve(RetrievalQuery('w','query',top_k=1))
    assert len(result)==1 and result[0].rerank_score is None
    assert retriever.last_rerank_trace['status']=='fallback'
