"""Serializable working state and guarded, injectable operational compaction.

Summaries NEVER become source evidence. A model can propose only hint text and
reference IDs; all protected state is copied by deterministic code. This is an
offline capability, not an automatically connected Agent compression loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Protocol

from .policy import ContextPolicy
from .recovery import EvidenceRecovery, EvidenceRef, RecoveryResult


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty text')


@dataclass(frozen=True, slots=True)
class RecordRef:
    workspace_id: str
    run_id: str
    kind: str
    locator: str
    sha256: str

    def __post_init__(self):
        for name in ('workspace_id', 'run_id', 'locator'):
            _text(getattr(self, name), name)
        if self.kind not in {'ledger', 'checkpoint'} or not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('invalid raw record reference')


@dataclass(frozen=True, slots=True)
class ToolState:
    call_id: str
    status: str
    error_code: str | None = None

    def __post_init__(self):
        _text(self.call_id, 'call_id')
        if self.status not in {'pending', 'completed', 'failed', 'blocked', 'unknown'}:
            raise ValueError('invalid tool status')
        if self.status in {'failed', 'blocked'}:
            _text(self.error_code, 'failure error_code')


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    question: str
    support_ids: tuple[str, ...]
    counter_ids: tuple[str, ...]

    def __post_init__(self):
        _text(self.question, 'conflict question')
        if not self.support_ids or not self.counter_ids:
            raise ValueError('conflicts require both sides')
        if type(self.support_ids) is not tuple or type(self.counter_ids) is not tuple:
            raise ValueError('conflict references must be tuples')


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingContext:
    workspace_id: str
    run_id: str
    user_goal: str
    ledger_ref: RecordRef
    checkpoint_ref: RecordRef
    schema_version: int = 1
    completed_subgoals: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    conflicts: tuple[EvidenceConflict, ...] = ()
    negative_evidence_ids: tuple[str, ...] = ()
    last_tool: ToolState | None = None
    failed_tools: tuple[ToolState, ...] = ()
    next_investigations: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    summary_text: str = ''
    summary_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unsupported working context schema')
        for name in ('workspace_id', 'run_id', 'user_goal'):
            _text(getattr(self, name), name)
        for name in ('completed_subgoals', 'unresolved_questions', 'negative_evidence_ids',
                     'next_investigations', 'operations', 'summary_evidence_ids'):
            values = getattr(self, name)
            if type(values) is not tuple:
                raise ValueError(f'{name} must be a tuple')
            for value in values:
                _text(value, name)
        for name, kind in (('ledger_ref', 'ledger'), ('checkpoint_ref', 'checkpoint')):
            ref = getattr(self, name)
            if not isinstance(ref, RecordRef) or (ref.workspace_id, ref.run_id, ref.kind) != (self.workspace_id, self.run_id, kind):
                raise ValueError('raw reference workspace/run/kind mismatch')
        for name, cls in (('evidence_refs', EvidenceRef), ('conflicts', EvidenceConflict), ('failed_tools', ToolState)):
            values = getattr(self, name)
            if type(values) is not tuple or any(not isinstance(v, cls) for v in values):
                raise ValueError(f'invalid {name}')
        if any(r.workspace_id != self.workspace_id for r in self.evidence_refs):
            raise ValueError('evidence reference workspace mismatch')
        ids = {r.evidence_id for r in self.evidence_refs}
        if len(ids) != len(self.evidence_refs) or len(ids) > 256:
            raise ValueError('duplicate or excessive evidence references')
        used = set(self.negative_evidence_ids) | set(self.summary_evidence_ids)
        used.update(i for c in self.conflicts for i in (*c.support_ids, *c.counter_ids))
        if not used.issubset(ids):
            raise ValueError('working state contains unknown evidence reference')
        if self.last_tool is not None and not isinstance(self.last_tool, ToolState):
            raise ValueError('invalid last tool state')
        if any(t.status not in {'failed', 'blocked'} for t in self.failed_tools):
            raise ValueError('failure history must contain failures')
        if self.last_tool and self.last_tool.status in {'failed', 'blocked'} and self.last_tool not in self.failed_tools:
            raise ValueError('last failure must remain in failure history')
        if not isinstance(self.summary_text, str) or len(self.summary_text) > 16384:
            raise ValueError('summary exceeds bounded hint size')
        embedded = set(re.findall(r'context_ev_v1_[a-zA-Z0-9_]+', self.summary_text))
        if not embedded.issubset(set(self.summary_evidence_ids)):
            raise ValueError('summary contains an undeclared evidence reference')
        if len(self.to_json().encode('utf-8')) > 1024 * 1024:
            raise ValueError('working context exceeds serialization limit')

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> WorkingContext:
        if not isinstance(text, str) or len(text.encode('utf-8')) > 1024 * 1024:
            raise ValueError('working context exceeds serialization limit')
        data = json.loads(text)
        for name in ('ledger_ref', 'checkpoint_ref'):
            data[name] = RecordRef(**data[name])
        data['evidence_refs'] = tuple(EvidenceRef.from_dict(v) for v in data['evidence_refs'])
        data['conflicts'] = tuple(EvidenceConflict(v['question'], tuple(v['support_ids']), tuple(v['counter_ids'])) for v in data['conflicts'])
        data['failed_tools'] = tuple(ToolState(**v) for v in data['failed_tools'])
        data['last_tool'] = ToolState(**data['last_tool']) if data['last_tool'] else None
        for name in ('completed_subgoals', 'unresolved_questions', 'negative_evidence_ids',
                     'next_investigations', 'operations', 'summary_evidence_ids'):
            data[name] = tuple(data[name])
        return cls(**data)

    def render(self) -> str:
        """Hints for a future model request; contains no source quotes or proof."""
        data = json.loads(self.to_json())
        data['evidence_refs'] = [r.evidence_id for r in self.evidence_refs]
        data['authority'] = 'UNTRUSTED_OPERATIONAL_HINT; restore original quotes before business conclusions'
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True, slots=True)
class CompactionRequest:
    state: WorkingContext
    max_tokens: int


@dataclass(frozen=True, slots=True)
class CompactionProposal:
    summary_text: str
    evidence_ids: tuple[str, ...]


class Compactor(Protocol):
    def compact(self, request: CompactionRequest) -> CompactionProposal: ...


@dataclass(frozen=True, slots=True)
class CompactionResult:
    status: str
    state: WorkingContext
    recovery: RecoveryResult
    before_tokens: int
    after_tokens: int
    before_bytes: int
    after_bytes: int

    @property
    def compression_ratio(self) -> float:
        return self.after_bytes / self.before_bytes


def compact_working_context(state: WorkingContext, *, compactor: Compactor,
                            recovery: EvidenceRecovery, max_tokens: int,
                            policy: ContextPolicy | None = None) -> CompactionResult:
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError('positive compaction token budget required')
    policy = policy or ContextPolicy()
    counter = policy.count
    # The compactor receives a defensive copy; it never receives raw ledger data.
    proposal = compactor.compact(CompactionRequest(WorkingContext.from_json(state.to_json()), max_tokens))
    if not isinstance(proposal, CompactionProposal):
        raise ValueError('compactor must return a CompactionProposal')
    compacted = replace(state, operations=(), summary_text=proposal.summary_text,
                        summary_evidence_ids=proposal.evidence_ids)
    restored = recovery.restore(workspace_id=state.workspace_id, refs=state.evidence_refs,
                                required_ids=tuple(r.evidence_id for r in state.evidence_refs))
    status = restored.status
    if (counter(compacted.render()) > min(max_tokens, policy.context_window_tokens - policy.output_reserve_tokens)
            or len(compacted.render().encode('utf-8')) > policy.max_request_bytes):
        status, compacted = 'budget_exhausted', state
    return CompactionResult(status, compacted, restored, counter(state.render()), counter(compacted.render()),
                            len(state.to_json().encode('utf-8')), len(compacted.to_json().encode('utf-8')))
