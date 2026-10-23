"""Explicit Runtime passage / semantic span / C reference mapping.

All reads go through TaskRelationTools' host-authorized current source interface.
No paths are accepted from the model, and no relation is inferred here.
"""
from urllib.parse import quote
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace

from linkloom.context.models import ContextResource, ResourceType
from linkloom.context.recovery import EvidenceRef, SourceSnapshot
from linkloom.evidence_identity import passage_evidence_id
from linkloom.runtime.errors import ValidationError
from linkloom.semantic_ingestion.document_text import parse_source_text, source_parse_session
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, ObservationRef, canonical_hash
from linkloom.semantic_ingestion.task_relation_validation import verify_evidence_quote
from linkloom.tools.semantic_context import build_semantic_context


class TaskEvidenceBridge:
    def __init__(self, tools):
        self.tools = tools
        self.workspace_id = tools.workspace_id
        self._captured = None
        self.snapshot_id = None

    def _read(self):
        if self._captured is not None:
            return self._captured
        raw, semantic = self.tools.context_sources()
        if semantic is not None:
            semantic = replace(semantic, source_registry=deepcopy(semantic.source_registry))
            built = build_semantic_context(semantic, expected_workspace_id=self.workspace_id)
            if not built.ready:
                raise ValidationError("Authorized source inventory is incomplete.",
                    details={"code": "CONTEXT_SOURCE_INCOMPLETE", "errors": list(built.errors)})
            return (semantic.artifacts, semantic.source_registry, semantic.authorized_artifact_versions,
                semantic.revoked_evidence_refs, semantic.revoked_observation_ids, semantic.observations, semantic)
        observations = tuple(getattr(row, "observation", row) for row in raw.observations)
        revoked = frozenset(o.observation_id for row, o in zip(raw.observations, observations)
                            if getattr(row, "source_status", "CURRENT") != "CURRENT")
        return raw.artifacts, deepcopy(raw.source_registry), frozenset(a.artifact_version_id for a in raw.artifacts), frozenset(), revoked, observations, None

    def _identity(self, data):
        artifacts, registry, authorized, revoked_spans, revoked_observations, observations, semantic = data
        spans = [s.evidence_span for a in artifacts for s in parse_source_text(a).segments]
        return canonical_hash(dict(workspace_id=self.workspace_id, versions=sorted(a.artifact_version_id for a in artifacts),
            observations=[ObservationRef.from_observation(o).to_dict() for o in observations],
            authorized=sorted(authorized), revoked_spans=sorted(revoked_spans), revoked_observations=sorted(revoked_observations),
            registry=[(s.evidence_ref, registry.has_evidence(self.workspace_id, s.evidence_ref),
                registry.evidence_hash(self.workspace_id, s.evidence_ref), registry.evidence_version(self.workspace_id, s.evidence_ref)) for s in spans],
            semantic_contract=(dict(question=semantic.question, endpoints=[(k, v.to_dict()) for k, v in semantic.endpoints],
                queries=[asdict(q) for q in semantic.queries], source_handles=semantic.source_handles,
                expected_versions=sorted(semantic.expected_artifact_versions), expected_observations=sorted(semantic.expected_observation_payloads),
                complete=semantic.inventory_complete, truncated=semantic.truncated, limits=asdict(semantic.limits)) if semantic else None)))

    @contextmanager
    def session(self):
        """One validated immutable snapshot for a full catalog/recovery operation.

        Check a fresh host snapshot again at the operation boundary. A snapshot
        that changes while reading is rejected rather than mixed with another.
        """
        if self._captured is not None:
            yield
            return
        with source_parse_session():
            captured = self._read()
            snapshot_id = self._identity(captured)
            self._captured = captured
            self.snapshot_id = snapshot_id
            succeeded = False
            try:
                yield
                succeeded = True
            finally:
                self._captured = None
                if succeeded and self._identity(self._read()) != snapshot_id:
                    raise ValidationError("Authorized snapshot changed during recovery.", details={"code": "CONTEXT_SNAPSHOT_CHANGED"})

    def semantic_snapshot(self):
        return self._read()[-1]

    def _source(self, artifact, *, authorized, current, active):
        logical_path = "/task-sources/" + quote(artifact.artifact_id, safe="")
        resource = ContextResource(workspace_id=artifact.workspace_id, resource_id=artifact.artifact_version_id,
            document_id=artifact.artifact_id, logical_path=logical_path, parent_path=None,
            version=1, source_timestamp=artifact.ingestion_time, content_hash=artifact.content_hash,
            source_ref=artifact.source_ref, resource_type=ResourceType.DOCUMENT,
            indexed_at=artifact.ingestion_time, index_version=1, active=active)
        return SourceSnapshot(resource, artifact.content, authorized=authorized, current=current)

    def read_source(self, workspace_id, resource_id):
        """Recheck current permission/version/registry/revocation on every read."""
        if workspace_id != self.workspace_id:
            return None
        if self._captured is None:
            try:
                with self.session():
                    return self.read_source(workspace_id, resource_id)
            except ValidationError:
                return None
        artifacts, registry, authorized, revoked_spans, revoked_observations, observations, semantic = self._read()
        matches = [a for a in artifacts if a.workspace_id == workspace_id and a.artifact_version_id == resource_id]
        if len(matches) != 1:
            return None
        artifact = matches[0]
        parsed = parse_source_text(artifact)
        active = not any(s.evidence_span.evidence_ref in revoked_spans for s in parsed.segments)
        active = active and not any(o.observation_id in revoked_observations
            and o.provenance.artifact_version_id == resource_id for o in observations)
        current = parsed.status == "COMPLETE" and not parsed.issues
        current = current and all(not verify_evidence_quote(EvidenceQuote(s.evidence_span,
            s.evidence_span.char_start, s.evidence_span.char_end, s.evidence_span.quote, "RELATION_PROPOSAL"),
            workspace_id, artifacts, registry)[0] for s in parsed.segments)
        if semantic is not None:
            current = current and semantic.inventory_complete and not semantic.truncated
            current = current and frozenset(a.artifact_version_id for a in artifacts) == semantic.expected_artifact_versions
        return self._source(artifact, authorized=resource_id in authorized, current=current, active=active)

    def catalog(self):
        if self._captured is None:
            with self.session():
                return self.catalog()
        artifacts, _, _, _, _, _, semantic = self._read()
        if semantic is not None:
            built = build_semantic_context(semantic, expected_workspace_id=self.workspace_id)
            if not built.ready:
                raise ValidationError("Authorized source inventory is incomplete.",
                    details={"code": "CONTEXT_SOURCE_INCOMPLETE", "errors": list(built.errors)})
        entries = []
        seen = set()
        for artifact in artifacts:
            source = self.read_source(self.workspace_id, artifact.artifact_version_id)
            if source is None or not source.authorized or not source.current or not source.resource.active:
                raise ValidationError("Original source is unavailable or not current.", details={"code": "CONTEXT_SOURCE_INCOMPLETE"})
            for segment in parse_source_text(artifact).segments:
                span = segment.evidence_span
                ref = EvidenceRef.from_snapshot(source, span.char_start, span.char_end)
                r = source.resource
                identity = passage_evidence_id(workspace_id=self.workspace_id, resource_id=r.resource_id,
                    document_id=r.document_id, logical_path=r.logical_path, source_ref=r.source_ref,
                    content_hash=r.content_hash, line_start=ref.line_start, line_end=ref.line_end, quote_hash=ref.quote_hash)
                if identity in seen:
                    raise ValidationError("Ambiguous Runtime passage identity.", details={"code": "CONTEXT_PASSAGE_IDENTITY_CONFLICT"})
                seen.add(identity)
                entries.append(dict(origin_ref=span.evidence_ref, origin_version=artifact.artifact_version_id,
                    authorized_snapshot_id=self.snapshot_id,
                    runtime_passage=dict(evidence_id=identity, resource_id=r.resource_id, document_id=r.document_id,
                        workspace_id=self.workspace_id, logical_path=r.logical_path, source_ref=r.source_ref,
                        content_sha256=r.content_hash, quote_sha256=ref.quote_hash,
                        char_start=ref.char_start, char_end=ref.char_end, line_start=ref.line_start, line_end=ref.line_end,
                        status="verified"), context_ref=ref.to_dict()))
                if len(entries) > 256:
                    raise ValidationError("Source reference budget exceeded.", details={"code": "CONTEXT_SOURCE_LIMIT"})
        return entries
