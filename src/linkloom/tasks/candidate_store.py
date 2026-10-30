"""Append-only generated-batch receipts, independent of extractor wire formats."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from linkloom.tasks.models import ConfidenceMetadata, TaskCandidateInput, canonical_json, required_text
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, digest
from linkloom.tasks.review_storage import ReviewStorage
from linkloom.tasks.store import TaskSchemaError, TaskStore


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    candidate: TaskCandidateInput
    original_payload_json: str
    reviewable: bool


@dataclass(frozen=True, slots=True)
class PreparedBatch:
    source_context: str
    candidates: tuple[PreparedCandidate, ...]


class GeneratedBatchAdapter(Protocol):
    """Future formats implement decoding/verification; storage stays unchanged."""
    format_version: str

    def encode(self, batch: object) -> dict: ...

    def prepare(self, connection, workspace_id, source_id, source_version_id,
                payload: dict, confidence: dict[str, ConfidenceMetadata]) -> PreparedBatch: ...


_COLUMNS = {
    'task_candidate_batch': {'id', 'workspace_id', 'source_id', 'source_version_id', 'format_version',
                             'payload_json', 'confidence_json', 'receipt_fingerprint', 'created_at'},
    'task_candidate_entry': {'sequence', 'batch_id', 'workspace_id', 'candidate_id', 'input_json',
                             'original_payload_json', 'reviewable'},
}
_DDL = (
    """CREATE TABLE task_candidate_batch (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, source_id TEXT NOT NULL,
        source_version_id TEXT NOT NULL, format_version TEXT NOT NULL, payload_json TEXT NOT NULL,
        confidence_json TEXT NOT NULL, receipt_fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
        UNIQUE(id,workspace_id),
        FOREIGN KEY(workspace_id,source_id,source_version_id)
            REFERENCES ll_source_version(workspace_id,source_id,artifact_version_id))""",
    """CREATE TABLE task_candidate_entry (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL, candidate_id TEXT NOT NULL, input_json TEXT NOT NULL,
        original_payload_json TEXT NOT NULL, reviewable INTEGER NOT NULL CHECK(reviewable IN (0,1)),
        UNIQUE(batch_id,candidate_id),
        FOREIGN KEY(batch_id,workspace_id) REFERENCES task_candidate_batch(id,workspace_id))""",
    'CREATE INDEX task_candidate_source ON task_candidate_batch(workspace_id,source_id,source_version_id)',
    'CREATE INDEX task_candidate_identity ON task_candidate_entry(workspace_id,candidate_id)',
)
_TRIGGERS = {
    f'{table}_append_only_{operation.lower()}': f"""CREATE TRIGGER {table}_append_only_{operation.lower()}
        BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT,'generated task candidates are append-only'); END"""
    for table in _COLUMNS for operation in ('UPDATE', 'DELETE')
}


def _default_adapter():
    from linkloom.tasks.meeting_adapter import MeetingV2BatchAdapter
    return MeetingV2BatchAdapter()


def _adapter_registry(adapter=None, adapters=()):
    """Trusted Host registrations; old v2 remains readable beside new formats."""
    if not isinstance(adapters, (tuple, list)):
        raise ValueError('adapters must be a sequence of trusted format adapters')
    default = _default_adapter()
    registry = {default.format_version: default}
    explicit = set()
    for item in (*adapters, *((adapter,) if adapter is not None else ())):
        version = required_text(getattr(item, 'format_version', None), 'format_version')
        if version != item.format_version or not all(callable(getattr(item, name, None)) for name in ('encode', 'prepare')):
            raise ValueError('invalid generated batch adapter contract')
        if version in explicit and registry[version] is not item:
            raise ValueError('duplicate adapter format registration')
        registry[version] = item
        explicit.add(version)
    return (adapter if adapter is not None else default), registry


def _select_adapter(registry, format_version):
    version = required_text(format_version, 'format_version')
    if version != format_version or version not in registry:
        raise ValueError('generated batch format has no trusted adapter')
    return registry[version]


def _confidence(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('confidence metadata must be keyed by candidate ID')
    result = {}
    for key, metadata in value.items():
        identifier = required_text(key, 'candidate_id')
        if identifier != key or not isinstance(metadata, ConfidenceMetadata):
            raise ValueError('confidence metadata requires canonical IDs and ConfidenceMetadata values')
        result[key] = metadata
    return result


def _decode_confidence(encoded):
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError('invalid stored confidence metadata')
    result = {}
    for key, item in value.items():
        if not isinstance(item, dict) or set(item) != {'confidence', 'confidence_reason'}:
            raise ValueError('invalid stored confidence metadata')
        result[key] = ConfidenceMetadata(**item)
    return _confidence(result)


def _receipt(workspace, source, version, format_version, payload_json, confidence_json):
    return digest([workspace, source, version, format_version, json.loads(payload_json), json.loads(confidence_json)])


class GeneratedTaskBatchStore:
    """Host-only intake of ALREADY generated batches. Never invokes a model.

    The caller owns TaskStore. The source catalogue and all candidate evidence
    are verified on its same locked write transaction before publishing anything.
    """
    def __init__(self, store: TaskStore, adapter: GeneratedBatchAdapter | None = None, *, adapters=()):
        self.store = store
        self.adapter, self._adapters = _adapter_registry(adapter, adapters)
        # Installs durable source epoch tracking even before a review service opens.
        self.review_storage = ReviewStorage(store)
        with store._transaction():
            connection = store._connection
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables.intersection(_COLUMNS):
                for statement in (*_DDL, *_TRIGGERS.values()):
                    connection.execute(statement)
            else:
                if not set(_COLUMNS).issubset(tables):
                    raise TaskSchemaError('incomplete generated candidate schema')
                for table, columns in _COLUMNS.items():
                    if {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')} != columns:
                        raise TaskSchemaError('unsupported generated candidate schema')
                stored = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
                normalize = lambda text: ' '.join(text.split()).rstrip(';')
                if any(name not in stored or normalize(stored[name]) != normalize(sql)
                       for name, sql in _TRIGGERS.items()):
                    raise TaskSchemaError('generated candidate history guards are missing or unsupported')

    def save(self, workspace_id, source_id, source_version_id, batch, *, confidence_by_candidate=None,
             format_version=None, now=None):
        workspace = required_text(workspace_id, 'workspace_id')
        source, version = required_text(source_id, 'source_id'), required_text(source_version_id, 'source_version_id')
        confidence = _confidence(confidence_by_candidate)
        adapter = _select_adapter(self._adapters, format_version if format_version is not None else self.adapter.format_version)
        # Canonical JSON freezes any mutable nested data before validation/write.
        payload_json = canonical_json(adapter.encode(batch))
        confidence_json = canonical_json({key: value.to_dict() for key, value in confidence.items()})
        fingerprint = _receipt(workspace, source, version, adapter.format_version, payload_json, confidence_json)
        identifier = 'task_batch_' + fingerprint
        when = self.store._when(now)
        with self.store._transaction():
            connection = self.store._connection
            prepared = adapter.prepare(connection, workspace, source, version, json.loads(payload_json), confidence)
            existing = connection.execute('SELECT * FROM task_candidate_batch WHERE id=?', (identifier,)).fetchone()
            if existing is not None:
                _read_batch(connection, existing, adapter)  # Idempotence must not hide corrupt evidence/rows.
                return _batch_view(existing)
            connection.execute("""INSERT INTO task_candidate_batch(id,workspace_id,source_id,source_version_id,
                format_version,payload_json,confidence_json,receipt_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (identifier, workspace, source, version, adapter.format_version, payload_json,
                 confidence_json, fingerprint, when.isoformat()))
            for item in prepared.candidates:
                if connection.execute('SELECT 1 FROM task_candidate_entry WHERE candidate_id=? AND workspace_id<>?',
                                      (item.candidate.candidate_id, workspace)).fetchone():
                    raise CandidateUnavailableError('candidate identity already belongs to another workspace')
                connection.execute("""INSERT INTO task_candidate_entry(batch_id,workspace_id,candidate_id,
                    input_json,original_payload_json,reviewable) VALUES(?,?,?,?,?,?)""",
                    (identifier, workspace, item.candidate.candidate_id, canonical_json(item.candidate.to_dict()),
                     item.original_payload_json, int(item.reviewable)))
            return _batch_view(connection.execute('SELECT * FROM task_candidate_batch WHERE id=?', (identifier,)).fetchone())

    def get_batch(self, workspace_id, batch_id):
        """Read complete diagnostics/history without presenting it as current approval."""
        workspace, identifier = required_text(workspace_id, 'workspace_id'), required_text(batch_id, 'batch_id')
        with self.store._transaction():
            row = self.store._connection.execute('SELECT * FROM task_candidate_batch WHERE workspace_id=? AND id=?',
                                                 (workspace, identifier)).fetchone()
            if row is None:
                raise CandidateUnavailableError('batch is unavailable in this workspace')
            # Historical bytes remain inspectable after source revision; report no currentness claim.
            _check_receipt(row, _select_adapter(self._adapters, row['format_version']))
            return _batch_view(row)


def _check_receipt(row, adapter):
    if (row['format_version'] != adapter.format_version
        or canonical_json(json.loads(row['payload_json'])) != row['payload_json']
        or canonical_json(json.loads(row['confidence_json'])) != row['confidence_json']
        or _receipt(row['workspace_id'], row['source_id'], row['source_version_id'], row['format_version'],
                    row['payload_json'], row['confidence_json']) != row['receipt_fingerprint']
        or row['id'] != 'task_batch_' + row['receipt_fingerprint']):
        raise ValueError('generated batch receipt integrity mismatch')


def _batch_view(row):
    return dict(id=row['id'], workspace_id=row['workspace_id'], source_id=row['source_id'],
                source_version_id=row['source_version_id'], format_version=row['format_version'],
                payload=json.loads(row['payload_json']), confidence=json.loads(row['confidence_json']),
                fingerprint=row['receipt_fingerprint'], created_at=row['created_at'])


def _read_batch(connection, row, adapter):
    _check_receipt(row, adapter)
    prepared = adapter.prepare(connection, row['workspace_id'], row['source_id'], row['source_version_id'],
                               json.loads(row['payload_json']), _decode_confidence(row['confidence_json']))
    expected = {item.candidate.candidate_id: item for item in prepared.candidates}
    stored = connection.execute('SELECT * FROM task_candidate_entry WHERE batch_id=? ORDER BY sequence', (row['id'],)).fetchall()
    if len(stored) != len(expected):
        raise ValueError('generated batch candidate inventory mismatch')
    for entry in stored:
        item = expected.get(entry['candidate_id'])
        if (item is None or entry['workspace_id'] != row['workspace_id']
            or canonical_json(item.candidate.to_dict()) != entry['input_json']
            or item.original_payload_json != entry['original_payload_json']
            or int(item.reviewable) != entry['reviewable']):
            raise ValueError('stored generated candidate integrity mismatch')
    return prepared


class StoredTaskCandidateSource:
    """Transaction-bound projection; full variants stay proposals until reviewed."""
    def __init__(self, adapter: GeneratedBatchAdapter | None = None, *, adapters=()):
        self.adapter, self._adapters = _adapter_registry(adapter, adapters)

    def list_candidates(self, connection, workspace_id):
        workspace = required_text(workspace_id, 'workspace_id')
        if not connection.in_transaction:
            raise ValueError('generated candidate verification requires an active transaction')
        if connection.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?', (workspace,)).fetchone() is None:
            raise CandidateUnavailableError('workspace is unavailable')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_candidate_batch'").fetchone() is None:
            return ()
        rows = connection.execute("""SELECT batch.* FROM task_candidate_batch AS batch JOIN ll_source AS source
            ON source.workspace_id=batch.workspace_id AND source.source_id=batch.source_id
                AND source.current_version_id=batch.source_version_id
            WHERE batch.workspace_id=? AND source.active=1 ORDER BY batch.id""", (workspace,)).fetchall()
        groups = {}
        try:
            for row in rows:
                prepared = _read_batch(connection, row, _select_adapter(self._adapters, row['format_version']))
                for item in prepared.candidates:
                    if item.reviewable:
                        groups.setdefault(item.candidate.candidate_id, {})[item.candidate.fingerprint] = (item, prepared.source_context)
            results = []
            for identifier in sorted(groups):
                variants = groups[identifier]
                signatures = sorted(variants)
                for fingerprint in signatures:
                    item, source_context = variants[fingerprint]
                    context = digest(dict(source=source_context, variants=signatures))
                    results.append(CandidateSnapshot(item.candidate, context, item.original_payload_json, len(signatures) > 1))
            return tuple(results)
        except (KeyError, TypeError, ValueError) as error:
            raise CandidateUnavailableError('generated candidate or original source failed verification') from error

    def version_proof(self, connection, workspace_id, source_id, source_version_id, **options):
        from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
        return LegacyTaskCandidateSource().version_proof(connection, workspace_id, source_id, source_version_id, **options)
