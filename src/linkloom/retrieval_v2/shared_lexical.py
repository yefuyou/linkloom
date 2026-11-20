"""Service-owned lexical indexes; no global cache or persisted user data."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock, RLock

from linkloom.indexing.bm25 import BM25Index
from linkloom.indexing.models import IndexDocument


@dataclass
class _WorkspaceLexicalState:
    lock: object = field(default_factory=RLock)
    index: BM25Index = field(default_factory=BM25Index)
    documents: dict[str, IndexDocument] | None = None

    def synchronize(self, documents: dict[str, IndexDocument]) -> BM25Index:
        # Caller holds the workspace lock through retrieval and materialization.
        if self.documents is None:
            self.index.build(documents.values())
        else:
            for resource_id in self.documents.keys() - documents.keys():
                workspace_id = self.documents[resource_id].workspace_id
                self.index.remove(workspace_id, resource_id)
            for resource_id, document in documents.items():
                if self.documents.get(resource_id) != document:
                    self.index.upsert(document)
        self.documents = documents.copy()
        return self.index


class WorkspaceLexicalIndexes:
    """One latest snapshot per workspace, owned by the hosting service.

    Different runs can authorize different source subsets. A workspace lock spans
    synchronization and search so each run sees exactly its own snapshot, even
    when an older run resumes after another run updated the shared index.
    """

    def __init__(self):
        self._lock = Lock()
        self._states: dict[str, _WorkspaceLexicalState] = {}

    @contextmanager
    def access(self, workspace_id: str):
        with self._lock:
            state = self._states.get(workspace_id)
            if state is None:
                state = self._states[workspace_id] = _WorkspaceLexicalState()
        with state.lock:
            yield state

    def clear(self):
        # The service calls this only after its workers have drained.
        with self._lock:
            self._states.clear()
