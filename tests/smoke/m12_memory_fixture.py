"""Provider-neutral, source-derived fixture wiring for the frozen mps-001 smoke.

The scoped constructor binding is necessary because RuntimeEngine has no adapter
injection parameter. It changes only harness composition, not Runtime V2 code.
Never use this process-scoped binding concurrently with another runtime.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import patch

from linkloom.agents import runtime_adapter as adapter_module
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory import (
    DecisionMaterializer, DecisionRecord, DecisionStatus,
    SourceReferenceRegistry, TemporalDecisionStore,
)
from linkloom.loader import VaultReader


def fixture_final(value, ref):
    return json.dumps({
        'schema_version': 'team-decision-result/v1',
        'decision': {'value': value, 'status': 'approved', 'evidence_refs': [ref]},
        'rationale': [], 'rejected_alternatives': [], 'actions': [], 'unresolved_items': [],
        'uncertainty': {'status': 'none', 'statement': None, 'unknown_fields': [], 'evidence_refs': []},
        'evidence_refs': [ref],
    })


@dataclass
class MemoryBinding:
    adapter: adapter_module.RuntimeAgentAdapter
    source_ref: str
    value: str
    constructions: int = 0


@contextmanager
def smoke_memory_binding(vault, index, db_path, *, seed=True):
    reader = VaultReader(vault_root=vault, index_path=index)
    source = next(d for d in reader.read_notes() if d.relative_path == '03-final-decision.md')
    if 'status: approved' not in source.content or 'authority: decision_record' not in source.content:
        raise ValueError('frozen approved decision source required')
    # Extract the existing Decision paragraph; never read evaluator/Gold fields.
    normalized = source.content.replace('\r\n', '\n')
    value = ' '.join(normalized.split('## Decision\n', 1)[1].split('\n## ', 1)[0].split())
    source_ref = source.relative_path
    workspace = reader.vault_root_fingerprint
    registry = SourceReferenceRegistry()
    registry.register_episode(workspace, source_ref)
    registry.register_evidence(workspace, source_ref, content_hash=source.content_sha256)
    store = TemporalDecisionStore(db_path, source_registry=registry)
    try:
        if seed:
            # Validate this deterministic source-derived materialization. This
            # observation belongs to fixture setup, never to the smoke model.
            TeamDecisionResult.from_grounded_final(fixture_final(value, source_ref), [source_ref])
            date_line = next(line for line in source.content.splitlines() if line.startswith('date: '))
            decision = DecisionRecord(
                decision_id='mps-001-current', workspace_id=workspace,
                subject_key='Atlas Lantern model provider', value=value,
                status=DecisionStatus.CURRENT,
                valid_from=datetime.fromisoformat(date_line[6:].strip()).replace(tzinfo=UTC),
                valid_to=None, supersedes_id=None, source_episode_id=source_ref,
                source_evidence_refs=(source_ref,), provenance_run_id='mps-001-frozen-source-fixture',
            )
            materializer = DecisionMaterializer(store)
            candidate = materializer.propose(decision, team_decision_contract_pass=True, grounding_pass=True)
            materializer.materialize(candidate, approved_by='explicit-frozen-smoke-fixture')
        adapter = adapter_module.RuntimeAgentAdapter(
            vault, index, decision_memory_store=store, retrieval_mode='hybrid',
        )
        binding = MemoryBinding(adapter, source_ref, value)

        def build(actual_vault, actual_index):
            if Path(actual_vault).resolve() != Path(vault).resolve() or Path(actual_index).resolve() != Path(index).resolve():
                raise ValueError('smoke adapter binding cannot cross workspaces')
            binding.constructions += 1
            return adapter

        with patch.object(adapter_module, 'RuntimeAgentAdapter', build):
            yield binding
    finally:
        store.close()
