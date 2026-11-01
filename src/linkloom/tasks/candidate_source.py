"""Product task candidate composition; formal task identity remains a human choice."""
from __future__ import annotations

from linkloom.tasks.candidate_store import StoredTaskCandidateSource
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import required_text
from linkloom.tasks.review_models import CandidateUnavailableError


class ProductTaskCandidateSource:
    """Read persisted legacy + generated proposals on the caller's transaction."""
    def __init__(self, generated_source=None):
        self.legacy = LegacyTaskCandidateSource()
        self.generated = generated_source or StoredTaskCandidateSource()

    def list_candidates(self, connection, workspace_id):
        workspace = required_text(workspace_id, 'workspace_id')
        if not connection.in_transaction:
            raise ValueError('candidate composition requires an active transaction')
        if connection.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?', (workspace,)).fetchone() is None:
            raise CandidateUnavailableError('workspace is unavailable')
        has_legacy = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_candidate'").fetchone()
        legacy = self.legacy.list_candidates(connection, workspace) if has_legacy else ()
        generated = self.generated.list_candidates(connection, workspace)
        if {item.candidate.candidate_id for item in legacy} & {item.candidate.candidate_id for item in generated}:
            raise CandidateUnavailableError('candidate ID is ambiguous across extractor contracts')
        return (*legacy, *generated)

    def version_proof(self, connection, workspace_id, source_id, source_version_id, **options):
        return self.legacy.version_proof(connection, workspace_id, source_id, source_version_id, **options)
