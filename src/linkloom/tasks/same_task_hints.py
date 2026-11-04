"""Opt-in, on-demand task hints. Ranking never grants merge authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import json
import os
from threading import Event, Lock, Thread
from urllib.parse import quote

from linkloom.tasks.models import TaskStatus
from linkloom.tasks.query import TaskQueryError
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, ReviewConflictError, digest


@dataclass(frozen=True)
class SameTaskHintConfig:
    enabled: bool = False
    # Provisional conservative defaults, NOT calibrated on the pending Dev set.
    min_local_score: float = 2.0
    min_rrf_score: float = 0.0325
    min_rrf_dense_score: float = 0.90
    model_dir: str | None = None
    min_title_similarity: float = 0.85

    def __post_init__(self):
        for value in (self.min_local_score, self.min_rrf_score, self.min_rrf_dense_score, self.min_title_similarity):
            if not math.isfinite(value):
                raise ValueError('same-task hint thresholds must be finite')
        if not 0 <= self.min_rrf_score <= 2 / 61 or not -1 <= self.min_rrf_dense_score <= 1:
            raise ValueError('same-task hint retrieval thresholds are out of range')
        if not -1 <= self.min_title_similarity <= 1:
            raise ValueError('same-task hint title threshold is out of range')

    @classmethod
    def from_env(cls):
        if os.environ.get('LINKLOOM_SAME_TASK_HINT', '').strip().lower() != 'on':
            return cls()
        return cls(True, *(float(os.environ.get(name, str(default))) for name, default in (
            ('LINKLOOM_SAME_TASK_HINT_MIN_LOCAL_SCORE', 2.0),
            ('LINKLOOM_SAME_TASK_HINT_MIN_RRF_SCORE', 0.0325),
            ('LINKLOOM_SAME_TASK_HINT_MIN_RRF_DENSE_SCORE', 0.90))),
            model_dir=os.environ.get('LINKLOOM_SAME_TASK_HINT_MODEL_DIR') or None,
            min_title_similarity=float(os.environ.get('LINKLOOM_SAME_TASK_HINT_MIN_TITLE_SIMILARITY', '0.85')))


def _description(snapshot):
    observation = json.loads(snapshot.original_payload_json).get('candidate', {}).get('task_observation')
    if observation is None:
        return [item.quote for item in snapshot.candidate.evidence]
    if observation['observation_id'] != snapshot.candidate.candidate_id:
        raise CandidateUnavailableError('description belongs to another candidate')
    parts = []
    for part in observation['field_evidence']:
        if part['field'] != 'description':
            continue
        if not any(reference.char_start is not None
                   and reference.char_start <= part['char_start'] < part['char_end'] <= reference.char_end
                   and reference.quote[part['char_start'] - reference.char_start:
                                       part['char_end'] - reference.char_start] == part['quote']
                   for reference in snapshot.candidate.evidence):
            raise CandidateUnavailableError('task description does not match its source range')
        parts.append(part['quote'])
    return parts


def _text(candidate, evidence, description):
    # Exact, version-verified task quotes provide the description; no generated summary.
    return '\n'.join(filter(None, (candidate.get('title'),
        *dict.fromkeys(description), candidate.get('owner'),
        *dict.fromkeys((item.get('source_name') or '') + ' ' + (item.get('meeting_time') or '')
                      for item in evidence))))


class SameTaskHintService:
    def __init__(self, review, config, *, embedder=None, reranker=None):
        self.review, self.config = review, config
        self.embedder, self.reranker = embedder, reranker
        self._owns_reranker = reranker is None
        self._inference_lock = Lock()
        self._preload_lock = Lock()
        self._preload_thread = None
        self._preload_state = 'idle'
        self._closed = Event()

    @property
    def preparing(self):
        with self._preload_lock:
            return self._preload_state == 'preparing'

    def start_preload(self):
        """Start one local-only loader; requests do not wait for model preparation."""
        if not self.config.enabled:
            return
        with self._preload_lock:
            if self._closed.is_set() or self._preload_state == 'preparing':
                return
            self._preload_state = 'preparing'
            self._preload_thread = Thread(target=self._preload, name='linkloom-same-task-preload', daemon=True)
            self._preload_thread.start()

    def _preload(self):
        state = 'unavailable'
        try:
            with self._inference_lock:
                if not self._closed.is_set():
                    self._local_reranker().prepare(cancel=self._closed)
                    state = 'ready'
        except Exception:
            # Missing dependencies/artifacts and failed loads retain the RRF fallback.
            pass
        finally:
            with self._preload_lock:
                self._preload_state = state

    def close(self):
        self._closed.set()
        with self._preload_lock:
            thread = self._preload_thread
        if thread is not None:
            thread.join()
        with self._inference_lock:
            if self._owns_reranker and self.reranker is not None:
                self.reranker.close()

    def _snapshot(self, workspace, candidate_id, fingerprint):
        review = self.review
        with review.request_scope(), review.store._transaction():
            review.require_workspace(workspace)
            incoming = review._current(workspace, candidate_id, fingerprint)
            review.store._unprocessed(incoming.candidate)
            incoming_proofs = review.merges._proofs(incoming)
            incoming_evidence = review.merges._evidence_view(workspace, incoming.candidate.evidence)
            tasks = []
            for task in review.store.list_tasks(workspace):
                if task.status not in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
                    continue
                latest = review._refresh(review.storage.latest(workspace, task_id=task.id), review.store._when(None))
                if latest is None or latest['review_status'] != 'approved' or latest['candidate'] is None:
                    continue
                try:
                    # Include all current field dependencies of a multi-meeting task.
                    review._validate_dependencies(latest)
                    snapshots = {row['candidate_fingerprint']: row['candidate'] for row in
                                 review.storage.history(workspace, task.id) if row['candidate'] is not None}
                    fingerprints = {latest['candidate_fingerprint']} | {
                        field['candidate_fingerprint'] for field in latest['field_sources'].values()}
                    references, proofs, descriptions = {}, [], []
                    for key in sorted(fingerprints):
                        snapshot = CandidateSnapshot.from_dict(snapshots[key])
                        if snapshot.candidate.workspace_id != workspace or snapshot.fingerprint != key:
                            raise CandidateUnavailableError('task evidence belongs to a different snapshot')
                        proofs.extend(review.merges._proofs(snapshot))
                        descriptions.extend(_description(snapshot))
                        for reference in snapshot.candidate.evidence:
                            references[digest(reference.to_dict())] = reference
                    evidence = review.merges._evidence_view(workspace, tuple(references.values()))
                except (CandidateUnavailableError, ReviewConflictError):
                    continue
                tasks.append(dict(task=task.to_dict(), review_revision=str(latest['sequence']),
                                  evidence=evidence, proofs=proofs, description=descriptions))
            value = dict(candidate=incoming.candidate.to_dict(), candidate_fingerprint=incoming.fingerprint,
                         evidence=incoming_evidence, proofs=incoming_proofs,
                         description=_description(incoming), tasks=tasks)
            return value, digest(value)

    def _local_reranker(self):
        if self.reranker is None:
            from huggingface_hub import snapshot_download
            from linkloom.rerank.local import LocalCrossEncoder
            from linkloom.rerank.model_cache import (BASE_MODEL, REVISIONS, artifact_directory,
                artifact_identity, cache_root, valid_artifact)
            from linkloom.rerank.pipeline import RerankPipeline
            directory = artifact_directory(BASE_MODEL)
            if not valid_artifact(directory, artifact_identity(BASE_MODEL)):
                raise RuntimeError('local reranker is not prepared')
            snapshot = self.config.model_dir or snapshot_download(BASE_MODEL, revision=REVISIONS[BASE_MODEL],
                cache_dir=str(cache_root() / 'hub'), local_files_only=True)
            self.reranker = RerankPipeline('local', local=LocalCrossEncoder(BASE_MODEL,
                model_dir=snapshot, onnx_path=str(directory / 'model-int8.onnx'), device='cpu', engine='onnx'))
        return self.reranker

    def _rank(self, snapshot):
        # Only local inference is serialized; no database lock/transaction is held here.
        while not self._inference_lock.acquire(timeout=0.05):
            if self.preparing:
                return [], 'preparing', 'model_preparing'
        try:
            if self.preparing:
                return [], 'preparing', 'model_preparing'
            return self._rank_local(snapshot)
        finally:
            self._inference_lock.release()

    def _rank_local(self, snapshot):
        from linkloom.indexing import BM25Index, IndexDocument, SentenceTransformerEmbedder, VectorIndex
        from linkloom.retrieval_v2.models import RetrievalQuery
        from linkloom.retrieval_v2.retrievers import BM25Retriever, DenseRetriever, HybridRetriever
        from linkloom.rerank.base import RerankCandidate
        candidate = snapshot['candidate']
        workspace = candidate['workspace_id']
        documents = [IndexDocument(workspace, row['task']['id'], row['task']['id'],
            '/tasks/' + quote(row['task']['id'], safe=''), row['text'], row['task']['id'])
            for row in snapshot['tasks']]
        # Reuse existing indexes only within this request. No dedicated index or result cache.
        lexical = BM25Index()
        lexical.build(documents)
        if self.embedder is None:
            self.embedder = SentenceTransformerEmbedder(local_files_only=True)
        dense = VectorIndex(self.embedder)
        dense.build(documents)
        hits = HybridRetriever(BM25Retriever(lexical), DenseRetriever(dense)).retrieve(
            RetrievalQuery(workspace, _text(candidate, snapshot['evidence'], snapshot['description']), top_k=10))
        # Relevance is not task identity. A separate conservative title cosine gate
        # reduces hints for related deliverables; it neither changes RRF nor merges tasks.
        import numpy as np
        tasks = {row['task']['id']: row['task'] for row in snapshot['tasks']}
        titles = [candidate.get('title') or '', *[tasks[hit.resource_id]['title'] for hit in hits]]
        vectors = np.asarray(self.embedder.encode(titles), dtype=float)
        norms = np.linalg.norm(vectors, axis=1)
        if vectors.ndim != 2 or len(vectors) != len(titles) or not np.isfinite(vectors).all() or (norms == 0).any():
            raise ValueError('invalid local title embeddings')
        vectors = vectors / norms[:, None]
        hits = [replace(hit, metadata={**hit.metadata, 'title_similarity': float(vectors[0] @ vectors[index])})
                for index, hit in enumerate(hits, 1)]
        texts = {row['task']['id']: row['text'] for row in snapshot['tasks']}
        try:
            pipeline = self._local_reranker()
            ranked = pipeline.rerank(_text(candidate, snapshot['evidence'], snapshot['description']),
                [RerankCandidate(hit, texts[hit.resource_id]) for hit in hits])
            if pipeline.last_trace.get('status') == 'success':
                return ranked, 'local', None
            if pipeline.last_trace.get('reason') == 'model_preparing':
                self.start_preload()
                return [], 'preparing', 'model_preparing'
        except Exception:
            # Remote providers are never constructed, including with LINKLOOM_RERANK=jev.
            pass
        return hits, 'rrf', 'local_rerank_unavailable'

    def select_hints(self, hints):
        """Insertion point for a future optional, separately authorized decision stage."""
        return hints

    def get_hints(self, workspace, candidate_id, fingerprint):
        if not self.config.enabled:
            return dict(enabled=False, hints=[])
        snapshot, token = self._snapshot(workspace, candidate_id, fingerprint)
        rows = snapshot['tasks']
        for row in rows:
            row['text'] = _text(row['task'], row['evidence'], row['description'])
        exact = sorted((row for row in rows if row['task']['title'] == snapshot['candidate']['title']),
                       key=lambda row: (row['task']['updated_at'], row['task']['id']), reverse=True)
        hints = [self._hint(snapshot, row, 'exact', None, None) for row in exact[:3]]
        mode, reason = 'exact', None
        if len(hints) < 3 and rows:
            try:
                ranked, mode, reason = self._rank(snapshot)
            except Exception:
                # Optional local retrieval dependencies/models may be absent. Exact hints survive.
                ranked, mode, reason = [], 'unavailable', 'local_retrieval_unavailable'
            by_id = {row['task']['id']: row for row in rows}
            seen = {hint['task']['id'] for hint in hints}
            for hit in ranked:
                if hit.resource_id in seen:
                    continue
                if mode == 'local':
                    score = hit.rerank_score
                    passes = score is not None and math.isfinite(score) and score >= self.config.min_local_score
                else:
                    score = hit.score
                    dense_score = hit.metadata.get('channel_scores', {}).get('dense')
                    passes = (math.isfinite(score) and score >= self.config.min_rrf_score
                              and dense_score is not None and math.isfinite(dense_score)
                              and dense_score >= self.config.min_rrf_dense_score)
                title_similarity = hit.metadata.get('title_similarity')
                passes = (passes and title_similarity is not None and math.isfinite(title_similarity)
                          and title_similarity >= self.config.min_title_similarity)
                if passes:
                    hints.append(self._hint(snapshot, by_id[hit.resource_id], mode, score, hit.score, title_similarity))
                    seen.add(hit.resource_id)
                if len(hints) == 3:
                    break
        hints = self.select_hints(hints)
        # Revalidate outside the inference lifetime. A→B→A invalidations remain latched by review.
        _, current_token = self._snapshot(workspace, candidate_id, fingerprint)
        if token != current_token:
            raise TaskQueryError('HINT_STALE', 'Task or source authorization changed; refresh the hints.')
        return dict(enabled=True, candidate_id=candidate_id, candidate_fingerprint=fingerprint,
                    hint_snapshot=token, mode=mode, reason=reason, status='preparing' if mode == 'preparing' else 'complete', hints=hints)

    @staticmethod
    def _hint(snapshot, row, mode, score, rrf_score, title_similarity=None):
        return dict(task=row['task'], task_review_revision=row['review_revision'],
                    match_kind='exact' if mode == 'exact' else 'semantic', score_kind=mode,
                    score=score, rrf_score=rrf_score, title_similarity=title_similarity, candidate_evidence=snapshot['evidence'],
                    task_evidence=row['evidence'])
