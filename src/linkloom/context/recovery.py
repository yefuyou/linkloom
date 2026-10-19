"""Read-only recovery of exact original evidence from immutable locators.

The loader must read authoritative current source/permission state on EVERY call.
It must not return a cached historical snapshot labelled current. This module
neither executes tools nor writes a Tool Ledger or Checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Callable, Mapping, Sequence

from .models import ContextResource, ResourceType, normalize_logical_path


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    resource: ContextResource
    content: str
    authorized: bool = True
    current: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ContextResource) or not isinstance(self.content, str):
            raise ValueError('source snapshot requires a resource and text')
        if type(self.authorized) is not bool or type(self.current) is not bool:
            raise ValueError('source permission and currentness must be booleans')


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    workspace_id: str
    evidence_id: str
    resource_id: str
    document_id: str
    logical_path: str
    source_ref: str
    source_version: int
    content_hash: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    quote_hash: str

    def __post_init__(self) -> None:
        for name in ('workspace_id', 'evidence_id', 'resource_id', 'document_id', 'source_ref'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} is required')
        if not isinstance(self.logical_path, str) or normalize_logical_path(self.logical_path) != self.logical_path:
            raise ValueError('logical_path must be canonical')
        for name in ('source_version', 'char_start', 'char_end', 'line_start', 'line_end'):
            if type(getattr(self, name)) is not int:
                raise ValueError('reference coordinates and version must be integers')
        if not (self.source_version > 0 and 0 <= self.char_start < self.char_end
                and 1 <= self.line_start <= self.line_end):
            raise ValueError('invalid reference range/version')
        if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h)
               for h in (self.content_hash, self.quote_hash)):
            raise ValueError('reference hashes must be lowercase SHA-256')

    def identity(self) -> str:
        # Character offsets distinguish even identical quotes on the same line.
        fields = self.to_dict()
        fields.pop('evidence_id')
        return 'context_ev_v1_' + digest(json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(',', ':')))

    @classmethod
    def from_snapshot(cls, source: SourceSnapshot, start: int, end: int) -> EvidenceRef:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.content):
            raise ValueError('invalid source range')
        r = source.resource
        ref = cls(r.workspace_id, 'pending', r.resource_id, r.document_id or '', r.logical_path,
                  r.source_ref, r.version, r.content_hash, start, end,
                  source.content.count('\n', 0, start) + 1,
                  source.content.count('\n', 0, end - 1) + 1, digest(source.content[start:end]))
        return cls.from_dict({**ref.to_dict(), 'evidence_id': ref.identity()})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping) -> EvidenceRef:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class RestoredEvidence:
    ref: EvidenceRef
    quote: str
    historical: bool


@dataclass(frozen=True, slots=True)
class RecoveryFailure:
    evidence_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    status: str
    evidence: tuple[RestoredEvidence, ...]
    failures: tuple[RecoveryFailure, ...]


class EvidenceRecovery:
    def __init__(self, loader: Callable[[str, str], SourceSnapshot | None], *, max_refs: int = 256,
                 max_source_bytes: int = 4 * 1024 * 1024, max_quote_bytes: int = 64 * 1024,
                 max_total_quote_bytes: int = 256 * 1024):
        if not callable(loader) or any(type(v) is not int or v <= 0 for v in
                (max_refs, max_source_bytes, max_quote_bytes, max_total_quote_bytes)):
            raise ValueError('a loader and positive recovery limits are required')
        self._loader = loader
        self.max_refs = max_refs
        self.max_source_bytes = max_source_bytes
        self.max_quote_bytes = max_quote_bytes
        self.max_total_quote_bytes = max_total_quote_bytes

    def restore(self, *, workspace_id: str, refs: Sequence[EvidenceRef],
                required_ids: Sequence[str] = (), historical: bool = False) -> RecoveryResult:
        if not isinstance(workspace_id, str) or not workspace_id.strip() or type(historical) is not bool:
            raise ValueError('workspace and historical mode are required')
        if len(refs) > self.max_refs or len(required_ids) > self.max_refs:
            raise ValueError('recovery reference limit exceeded')
        restored = []
        restored_bytes = 0
        failures = []
        seen: dict[str, EvidenceRef] = {}
        for ref in refs:
            if not isinstance(ref, EvidenceRef):
                raise TypeError('refs must contain EvidenceRef values')
            if ref.evidence_id in seen:
                if seen[ref.evidence_id] != ref:
                    failures.append(RecoveryFailure(ref.evidence_id, 'EVIDENCE_REF_CONFLICT'))
                    restored = [e for e in restored if e.ref.evidence_id != ref.evidence_id]
                    restored_bytes = sum(len(e.quote.encode('utf-8')) for e in restored)
                continue
            seen[ref.evidence_id] = ref
            reason = None
            if ref.workspace_id != workspace_id:
                reason = 'WORKSPACE_MISMATCH'
            else:
                try:
                    source = self._loader(workspace_id, ref.resource_id)
                except (OSError, LookupError):
                    source = None
                if source is None:
                    reason = 'SOURCE_NOT_FOUND'
                else:
                    reason = self._validate(ref, source, historical)
                    if reason is None and restored_bytes + len(source.content[ref.char_start:ref.char_end].encode('utf-8')) > self.max_total_quote_bytes:
                        reason = 'RECOVERY_BUDGET'
            if reason:
                failures.append(RecoveryFailure(ref.evidence_id, reason))
            else:
                quote = source.content[ref.char_start:ref.char_end]
                restored_bytes += len(quote.encode('utf-8'))
                restored.append(RestoredEvidence(ref, quote, not source.current))
        for missing in sorted(set(required_ids) - {e.ref.evidence_id for e in restored}):
            if not any(f.evidence_id == missing for f in failures):
                failures.append(RecoveryFailure(missing, 'REFERENCE_NOT_FOUND'))
        return RecoveryResult('incomplete' if failures else 'complete', tuple(restored), tuple(failures))

    def _validate(self, ref: EvidenceRef, source: SourceSnapshot, historical: bool) -> str | None:
        if not isinstance(source, SourceSnapshot):
            return 'SOURCE_INVALID'
        r = source.resource
        if r.workspace_id != ref.workspace_id:
            return 'WORKSPACE_MISMATCH'
        if not source.authorized:
            return 'SOURCE_FORBIDDEN'
        if r.active is not True:
            return 'SOURCE_INACTIVE'
        if not source.current and not historical:
            return 'SOURCE_NOT_CURRENT'
        if (r.resource_id, r.document_id, r.logical_path, r.source_ref, r.resource_type) != (
                ref.resource_id, ref.document_id, ref.logical_path, ref.source_ref, ResourceType.DOCUMENT):
            return 'SOURCE_IDENTITY_MISMATCH'
        if r.version != ref.source_version:
            return 'SOURCE_VERSION_MISMATCH'
        if len(source.content) > self.max_source_bytes or len(source.content.encode('utf-8')) > self.max_source_bytes:
            return 'SOURCE_TOO_LARGE'
        quote = source.content[ref.char_start:ref.char_end]
        if len(quote) > self.max_quote_bytes or len(quote.encode('utf-8')) > self.max_quote_bytes:
            return 'QUOTE_TOO_LARGE'
        if r.content_hash != ref.content_hash or digest(source.content) != ref.content_hash:
            return 'SOURCE_HASH_MISMATCH'
        if ref.char_end > len(source.content) or digest(source.content[ref.char_start:ref.char_end]) != ref.quote_hash:
            return 'QUOTE_HASH_MISMATCH'
        if (source.content.count('\n', 0, ref.char_start) + 1,
                source.content.count('\n', 0, ref.char_end - 1) + 1) != (ref.line_start, ref.line_end):
            return 'SOURCE_SPAN_MISMATCH'
        if ref.evidence_id != ref.identity():
            return 'EVIDENCE_IDENTITY_MISMATCH'
        return None
