"""Bounded, source-verified investigation leads. No assessments or persistence.

The caller supplies an authorized current Workspace snapshot. Candidate priority
is an ordering rule, never confidence in a relationship. Revalidate against a
fresh snapshot before routing endpoints to the existing P1/P2/P3 validators.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from itertools import combinations
import re

from linkloom.decision_memory.models import normalize_temporal_component as normalize
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_observation_query import TaskObservationRead
from linkloom.semantic_ingestion.task_relation_models import (
    ChangeRef, EvidenceQuote, ObservationRef, canonical_hash,
)
from linkloom.semantic_ingestion.task_relation_validation import (
    _aliases, _keys, _namespaces, _NAMESPACE, _quote, _sentences,
    verify_evidence_quote, verify_relation_inputs,
)


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    max_observations: int = 128
    max_artifacts: int = 64
    max_source_chars: int = 500_000
    max_candidates: int = 100
    max_per_observation: int = 10
    max_context_signals: int = 128
    max_generated_candidates: int = 1000

    def __post_init__(self):
        ceilings = (512, 256, 2_000_000, 2000, 512, 256, 5000)
        for name, ceiling in zip(self.__dataclass_fields__, ceilings):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"invalid discovery limit: {name}")


@dataclass(frozen=True, slots=True)
class RelationCandidate:
    subject: ObservationRef
    object: ObservationRef | ChangeRef
    predicate_hint: str
    priority: int
    reason_codes: tuple[str, ...]
    evidence: tuple[EvidenceQuote, ...]
    uncertainty: tuple[str, ...]
    candidate_id: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.subject, ObservationRef):
            raise ValueError("candidate requires an observation subject")
        shape = ChangeRef if self.predicate_hint == "AFFECTED_BY" else ObservationRef
        if self.predicate_hint not in {"SAME_TASK", "RELATED_TO", "AFFECTED_BY", "REVISES_PLAN"} or not isinstance(self.object, shape):
            raise ValueError("invalid candidate endpoint/predicate shape")
        if self.subject.workspace_id != self.object.workspace_id:
            raise ValueError("cross-workspace candidate")
        if self.subject == self.object or type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("invalid candidate priority or self-edge")
        for name in ("reason_codes", "uncertainty"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or any(not isinstance(v, str) or not v for v in values):
                raise ValueError("candidate needs reasons and uncertainty")
            object.__setattr__(self, name, tuple(sorted(set(values))))
        if "VERIFIER_REQUIRED" not in self.uncertainty:
            raise ValueError("discovery cannot establish a relation")
        if not isinstance(self.evidence, tuple) or not self.evidence or any(
            not isinstance(e, EvidenceQuote) or e.span.workspace_id != self.subject.workspace_id for e in self.evidence
        ):
            raise ValueError("candidate requires workspace-bound evidence")
        unique = {canonical_hash(e.to_dict()): e for e in self.evidence}
        object.__setattr__(self, "evidence", tuple(unique[k] for k in sorted(unique)))
        if self.predicate_hint in {"SAME_TASK", "RELATED_TO"} and self.subject.observation_id > self.object.observation_id:
            subject, target = self.object, self.subject
            object.__setattr__(self, "subject", subject)
            object.__setattr__(self, "object", target)
        object.__setattr__(self, "candidate_id", "task_relation_candidate_v1_" + canonical_hash(self._payload()))

    def _payload(self):
        return dict(schema_version="task-relation-candidate/v1", rule_version="candidate-discovery/v1",
                    phase="DISCOVERED", relation_status="UNCONFIRMED", authority="SUPPORTING_ONLY",
                    subject=self.subject.to_dict(), object=self.object.to_dict(), predicate_hint=self.predicate_hint,
                    priority=self.priority, reason_codes=list(self.reason_codes),
                    evidence=[e.to_dict() for e in self.evidence], uncertainty=list(self.uncertainty))

    def to_dict(self):
        return dict(candidate_id=self.candidate_id, **self._payload())

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("candidate must be an object")
        try:
            target = data["object"]
            if data["predicate_hint"] == "AFFECTED_BY":
                target = {k: v for k, v in target.items() if k != "endpoint_type"}
                target = ChangeRef(**{**target, "evidence": EvidenceQuote.from_dict(target["evidence"])})
            else:
                target = ObservationRef.from_dict(target)
            candidate = cls(ObservationRef.from_dict(data["subject"]), target, data["predicate_hint"], data["priority"],
                            tuple(data["reason_codes"]), tuple(EvidenceQuote.from_dict(e) for e in data["evidence"]),
                            tuple(data["uncertainty"]))
            if candidate.to_dict() != data:
                raise ValueError("altered or noncanonical candidate")
            return candidate
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("malformed candidate") from error


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: tuple[RelationCandidate, ...]
    rejected: tuple[dict, ...]
    eligible_observations: int
    considered_candidates: int
    limits: DiscoveryLimits
    input_counts: dict[str, int]
    generated_candidates: int
    endpoint_fanout: dict[str, int]
    truncation_reasons: tuple[str, ...]

    @property
    def truncated(self):
        return bool(self.truncation_reasons)

    def to_dict(self):
        return dict(candidates=[c.to_dict() for c in self.candidates], rejected=list(self.rejected),
                    eligible_observations=self.eligible_observations, considered_candidates=self.considered_candidates,
                    returned_candidates=len(self.candidates), truncated=self.truncated, limits=asdict(self.limits),
                    input_counts=dict(self.input_counts), generated_candidates=self.generated_candidates,
                    endpoint_fanout=dict(self.endpoint_fanout), max_endpoint_fanout=max(self.endpoint_fanout.values(), default=0),
                    truncation_reasons=list(self.truncation_reasons))


def _raw(observation):
    return _quote(observation, 0, len(observation.raw_text), "RELATION_PROPOSAL")


def _snapshot(workspace_id, observations, artifacts, registry, limits):
    if not isinstance(workspace_id, str) or not workspace_id.strip() or workspace_id != workspace_id.strip():
        raise ValueError("workspace_id must be explicit")
    for values, bound, kind in ((observations, limits.max_observations, "observation"),
                                 (artifacts, limits.max_artifacts, "artifact")):
        if not isinstance(values, (tuple, list)) or len(values) > bound:
            raise ValueError(f"bounded {kind} snapshot required")
    if not isinstance(registry, SourceReferenceRegistry) or any(not isinstance(a, RawArtifact) for a in artifacts):
        raise ValueError("typed sources and registry required")
    if sum(len(a.content) for a in artifacts) > limits.max_source_chars:
        raise ValueError("source character budget exceeded")
    sources = {}
    for artifact in artifacts:
        if artifact.workspace_id != workspace_id:
            continue
        prior = sources.get(artifact.artifact_id)
        if prior is not None and (prior.artifact_version_id != artifact.artifact_version_id or prior.source_ref != artifact.source_ref):
            raise ValueError("supply one current version per artifact identity")
        sources[artifact.artifact_id] = artifact
    accepted, rejected, payloads = {}, [], {}
    for row in observations:
        observation = row.observation if isinstance(row, TaskObservationRead) else row
        if not isinstance(observation, TaskObservation):
            raise ValueError("typed task observations required")
        oid = observation.observation_id
        ref = ObservationRef.from_observation(observation)
        payloads.setdefault(oid, set()).add(ref.payload_hash)
        if observation.provenance.workspace_id != workspace_id:
            errors = {"CROSS_WORKSPACE"}
        elif isinstance(row, TaskObservationRead) and (row.source_status != "CURRENT" or row.extraction_conflict):
            errors = {"QUERY_ROW_NOT_CURRENT_OR_CONFLICTED"}
        else:
            errors, _, _, _ = verify_relation_inputs((observation,), tuple(sources.values()), registry)
        if errors:
            rejected.append(dict(observation_id=oid, reason_codes=sorted(errors)))
        else:
            accepted[oid] = observation
    for oid, hashes in payloads.items():
        if len(hashes) > 1:
            accepted.pop(oid, None)
            rejected.append(dict(observation_id=oid, reason_codes=["EXTRACTION_CONFLICT"]))
    return tuple(accepted[k] for k in sorted(accepted)), tuple(sources[k] for k in sorted(sources)), rejected


def _context_signals(artifacts, workspace_id, registry, limit):
    """Only direct source declarations; aliases never become graph closure."""
    aliases, changes = [], []
    alias_pattern = re.compile(r'\s*(?:项目说明[：:]\s*)?本工作区的[“「]?([^“”「」。]+?)[”」]?是[“「]?'
                               r'([^“”「」。]+?)[”」]?的内部代号(?:，两者在本期([^。]+?)资料中指同一项目)?[。]?\s*')
    change_pattern = re.compile(r'(?P<project>[^，,。；;\s]+项目)(?:的)?(?:短信(?:服务)?)?供应商(?:由|从)'
                                r'(?P<before>[^，,。；;\s]+?)(?:改为|改用|改成)(?P<after>[^，,。；;\s]+)')
    for artifact in artifacts:
        for segment in parse_source_text(artifact).segments:
            span = segment.evidence_span
            full = EvidenceQuote(span, span.char_start, span.char_end, span.quote, "RELATION_PROPOSAL")
            errors, _ = verify_evidence_quote(full, workspace_id, artifacts, registry)
            if errors:
                continue
            for start, end, sentence, assertive in _sentences(span.quote):
                quote = EvidenceQuote(span, span.char_start + start, span.char_start + end,
                                      span.quote[start:end], "RELATION_PROPOSAL")
                # Literal quotes around alias names are part of the naming grammar.
                alias = alias_pattern.fullmatch(quote.quote)
                if alias and assertive and sentence.lstrip().startswith(("项目说明", "本工作区")):
                    aliases.append((normalize(alias[1]), normalize(alias[2]), alias[3], quote))
                if assertive:
                    for match in change_pattern.finditer(sentence):
                        if normalize(match["before"]) == normalize(match["after"]):
                            continue
                        changes.append(ChangeRef(workspace_id, match["project"], match["before"], match["after"], quote))
                if len(aliases) + len(changes) > limit:
                    raise ValueError("context signal budget exceeded")
    return aliases, changes


def _project_bridge(left, right, aliases):
    if left.project is None or right.project is None:
        return None
    a, b = normalize(left.project), normalize(right.project)
    if a == b:
        return ()
    contexts = (left.provenance.quote, right.evidence.span.quote if isinstance(right, ChangeRef) else right.provenance.quote)
    for origin, target, scope, evidence in aliases:
        if {a, b} == {origin, target} and (scope is None or all(scope in context for context in contexts)):
            return (evidence,)
    return None


def _namespace(observation, artifacts):
    span = observation.provenance
    artifact = next(a for a in artifacts if a.artifact_id == span.artifact_id)
    if any(m.start() < span.char_start or m.end() > span.char_end for m in _NAMESPACE.finditer(artifact.content)):
        return None
    values = {name for name, _ in _namespaces(observation)}
    return values if len(values) <= 1 and None not in values else None


def _lexical_tokens(observation):
    text = observation.description or ""
    for value in (observation.project, observation.vendor, observation.owner):
        if value:
            text = text.replace(value, " ")
    tokens = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for match in re.finditer(r"[\u4e00-\u9fff]+", text):
        tokens.update(match[0][i:i + 2] for i in range(len(match[0]) - 1))
    return tokens - {"项目", "任务", "负责", "完成", "截止", "日期", "计划", "供应", "应商"}


def _reference_signals(subject, target, keys, sentences, source_aliases):
    for start, end, sentence, assertive in sentences:
        file_names = [name for name in source_aliases[target.provenance.artifact_id] if re.search(
            r"(?<![A-Za-z0-9_./\\-])" + re.escape(name) + r"(?![A-Za-z0-9_./\\-])", sentence)]
        file_match = bool(file_names)
        mentioned_keys = {k for k in keys if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(k) + r"(?![A-Za-z0-9_-])", sentence)}
        self_cue = re.search(r"本任务|本条|此任务|该任务|参考|回指|依赖|修订|取代", sentence)
        compatible = subject.project and target.project and normalize(subject.project) == normalize(target.project)
        if not self_cue or not (file_match or (mentioned_keys and compatible)):
            continue
        # A qualified file reference only selects the named key, never every task in the file.
        if file_match and re.search(r"[A-Za-z]+-\d+", sentence) and not mentioned_keys:
            continue
        predicate = "REVISES_PLAN" if re.search(r"修订|改期|取代.*计划|替代.*计划", sentence) else (
            "SAME_TASK" if re.search(r"同一(?:个)?(?:业务)?任务", sentence) else "RELATED_TO")
        uncertainty = ["REFERENCE_TARGET_REQUIRES_VERIFICATION"]
        if any(sum(name in names for names in source_aliases.values()) > 1 for name in file_names):
            uncertainty.append("AMBIGUOUS_FILE_REFERENCE")
        if not assertive:
            uncertainty.append("NONASSERTIVE_REFERENCE")
        if not keys or not mentioned_keys:
            uncertainty.append("UNQUALIFIED_FILE_REFERENCE")
        yield predicate, _quote(subject, start, end, "UNRESOLVED_REFERENCE"), uncertainty


def discover_candidates(workspace_id: str, *, observations, artifacts, source_registry,
                        limits: DiscoveryLimits | None = None, anchor_ids: tuple[str, ...] | None = None) -> DiscoveryResult:
    """Discover within a complete bounded snapshot; never fetch more data or write.

    Optional anchor_ids selects incident candidates for one or more observations.
    This does not remove competing observations from reference disambiguation.
    Query rows are accepted directly, but their status alone is never trusted.
    """
    limits = limits or DiscoveryLimits()
    if not isinstance(limits, DiscoveryLimits):
        raise ValueError("typed limits required")
    inventory, sources, rejected = _snapshot(workspace_id, observations, artifacts, source_registry, limits)
    if anchor_ids is not None and (not isinstance(anchor_ids, tuple) or len(anchor_ids) > limits.max_observations
                                  or any(not isinstance(i, str) or not i for i in anchor_ids)):
        raise ValueError("bounded anchor ids required")
    anchors = None if anchor_ids is None else set(anchor_ids)
    aliases, changes = _context_signals(sources, workspace_id, source_registry, limits.max_context_signals)
    proposals = {}
    generated = 0
    keys = {o.observation_id: {k for k, _ in _keys(o)} for o in inventory}
    namespaces = {o.observation_id: _namespace(o, sources) for o in inventory}
    tokens = {o.observation_id: _lexical_tokens(o) for o in inventory}
    sentences = {o.observation_id: tuple(_sentences(o.raw_text)) for o in inventory}
    source_aliases = {a.artifact_id: _aliases(a) for a in sources}
    references = {o.observation_id: ObservationRef.from_observation(o) for o in inventory}
    raw_quotes = {o.observation_id: _raw(o) for o in inventory}

    def add(subject, target, predicate, priority, reasons, evidence=(), uncertainty=()):
        nonlocal generated
        ids = [o.observation_id for o in (subject, target) if isinstance(o, TaskObservation)]
        if anchors is not None and not anchors.intersection(ids):
            return
        # Bound construction, hashing and merging, not just final output length.
        # Fail explicitly rather than release a silently incomplete ranked pool.
        if generated >= limits.max_generated_candidates:
            raise ValueError("candidate generation budget exceeded; narrow the snapshot or select anchors")
        generated += 1
        refs = [references[subject.observation_id],
                references[target.observation_id] if isinstance(target, TaskObservation) else target]
        quotes = [raw_quotes[subject.observation_id],
                  *([raw_quotes[target.observation_id]] if isinstance(target, TaskObservation) else [target.evidence]), *evidence]
        candidate = RelationCandidate(*refs, predicate, priority, tuple(reasons), tuple(quotes),
                                      ("VERIFIER_REQUIRED", *uncertainty))
        key = (candidate.predicate_hint, canonical_hash(candidate.subject.to_dict()), canonical_hash(candidate.object.to_dict()))
        prior = proposals.get(key)
        if prior:
            candidate = RelationCandidate(candidate.subject, candidate.object, predicate, max(prior.priority, priority),
                (*prior.reason_codes, *candidate.reason_codes), (*prior.evidence, *candidate.evidence),
                (*prior.uncertainty, *candidate.uncertainty))
        proposals[key] = candidate

    for left, right in combinations(inventory, 2):
        bridge = _project_bridge(left, right, aliases)
        shared = keys[left.observation_id] & keys[right.observation_id]
        if shared and bridge is not None and namespaces[left.observation_id] is not None and namespaces[left.observation_id] == namespaces[right.observation_id]:
            uncertainty = ("KEY_REUSE_NOT_EXCLUDED",)
            if any(len(keys[o.observation_id]) > 1 for o in (left, right)):
                uncertainty += ("AMBIGUOUS_TASK_KEY",)
            add(left, right, "SAME_TASK", 100, ("SCOPED_TASK_KEY",), bridge,
                uncertainty + (("ALIAS_SCOPE_REQUIRES_VERIFICATION",) if bridge else ()))
        for subject, target in ((left, right), (right, left)):
            for predicate, quote, uncertainty in _reference_signals(subject, target, keys[target.observation_id],
                                                                  sentences[subject.observation_id], source_aliases):
                add(subject, target, predicate, 95, ("EXPLICIT_FILE_TASK_REFERENCE",), (quote,), uncertainty)
        if bridge is not None:
            overlap = len(tokens[left.observation_id] & tokens[right.observation_id]) >= 2
            reasons = ("PROJECT_ALIAS_SCOPE" if bridge else "PROJECT_SCOPE",)
            if overlap:
                reasons += ("LEXICAL_OVERLAP_WITHIN_PROJECT",)
            add(left, right, "RELATED_TO", 40 if overlap else 25, reasons, bridge,
                ("PROJECT_COOCCURRENCE_ONLY",))
    for observation in inventory:
        for change in changes:
            bridge = _project_bridge(observation, change, aliases)
            if bridge is not None and observation.vendor and normalize(observation.vendor) in {
                normalize(change.before_vendor), normalize(change.after_vendor)}:
                add(observation, change, "AFFECTED_BY", 60, ("PROJECT_VENDOR_CHANGE",), bridge,
                    uncertainty=("SOURCE_CLAIM_ONLY", "DIRECT_IMPACT_UNPROVEN", "DELAY_UNPROVEN") +
                    (("ALIAS_SCOPE_REQUIRES_VERIFICATION",) if bridge else ()))
    ranked = sorted(proposals.values(), key=lambda c: (-c.priority, c.candidate_id))
    selected, counts, truncation = [], Counter(), set()
    for candidate in ranked:
        endpoints = [r.observation_id for r in (candidate.subject, candidate.object) if isinstance(r, ObservationRef)]
        full = len(selected) >= limits.max_candidates
        fanout_full = any(counts[i] >= limits.max_per_observation for i in endpoints)
        if full:
            truncation.add("MAX_CANDIDATES")
        if fanout_full:
            truncation.add("MAX_PER_OBSERVATION")
        if not full and not fanout_full:
            selected.append(candidate)
            counts.update(endpoints)
    return DiscoveryResult(tuple(selected), tuple(sorted(rejected, key=lambda r: (r["observation_id"], r["reason_codes"]))),
                           len(inventory), len(ranked), limits,
                           dict(observation_rows=len(observations), artifact_rows=len(artifacts),
                                source_characters=sum(len(a.content) for a in artifacts),
                                current_workspace_artifacts=len(sources), context_signals=len(aliases) + len(changes)),
                           generated, {o.observation_id: counts[o.observation_id] for o in inventory}, tuple(sorted(truncation)))


def revalidate_candidate(candidate: RelationCandidate, workspace_id: str, *, observations, artifacts,
                         source_registry) -> bool:
    """Replay signals and dependency versions; cached output grants no authority."""
    if not isinstance(candidate, RelationCandidate) or candidate.subject.workspace_id != workspace_id:
        return False
    try:
        result = discover_candidates(workspace_id, observations=observations, artifacts=artifacts,
            source_registry=source_registry, anchor_ids=(candidate.subject.observation_id,),
            limits=DiscoveryLimits(512, 256, 2_000_000, 2000, 512, 256, 5000))
    except ValueError:
        return False
    return candidate in result.candidates
