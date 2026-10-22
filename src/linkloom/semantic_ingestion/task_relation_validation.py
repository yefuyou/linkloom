"""Conservative, deterministic identity resolution over verified M1 observations.

Only scoped ASCII ticket keys and bounded Chinese self-reference statements are
recognized. Similarity, owners, dates and arrival order never establish identity.
The caller supplies a bounded source/observation inventory, not a global graph.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import (
    EvidenceQuote, ObservationRef, RelationAssessment, SourceDependency,
)


_KEY_TEXT = r"[A-Za-z][A-Za-z0-9_]{0,31}-[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_KEY = re.compile(_KEY_TEXT + r"(?![A-Za-z0-9_-])")
_OWN_KEY = re.compile(r"(?:任务(?:编号|ID)?|工单(?:编号|ID)?|(?i:task(?:\s+(?:id|key))?|ticket(?:\s+id)?))"
                      r"\s*[:：#]?\s*(?P<key>" + _KEY_TEXT + r")(?![A-Za-z0-9_-])")
_NAMESPACE = re.compile(r"(?:系统|命名空间|看板|\b(?i:system|namespace|board))\s*[:：]\s*(?P<name>[^，,。；;\s]+)")
_NONASSERTIVE = re.compile(
    r"如果|假如|倘若|假设|若|只有|仅在|建议|考虑|可能|或许|是否|尚待|待确认|待核对|不确定|"
    r"尚未确认|未确认|无法确认|不能确认|没有证据|不代表|不意味着|并非不是|不是不|"
    r"引用|示例|例句|原话|摘录|模板|据说|听说|有人说|\b(?:if|unless|suppose|propose|uncertain)\b", re.I)
_SAME = re.compile(r"同一(?:个)?(?:业务)?任务|相同的(?:业务)?任务")
_SELF = re.compile(r"本条|本任务|该任务|此任务|这条|这是")
_SELF_HEAD = re.compile(r"(?:本条|本任务|该任务|此任务|这条)(?:对应|就是|仍是|仍为|是|为|与|和)\s*$")
_NEG_HEAD = re.compile(r"(?:(?:本条|本任务|该任务|此任务|这条)\s*)?(?:不是|并非|不属于|不同于)\s*$")
_NEG_SAME = re.compile(r"(?:不是|并非|不属于)\s*(?:同一(?:个)?(?:业务)?任务|相同的(?:业务)?任务)")
_REUSE = re.compile(r"编号.{0,12}(?:复用|重复|冲突)|(?:复用|重复).{0,12}编号|无法唯一指代")


def _sentences(text):
    """Mask quoted/code contexts without changing source offsets; scope modals locally."""
    masked = list(text)
    fenced = False
    for line in re.finditer(r"[^\n]*(?:\n|$)", text):
        stripped = line.group().lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            hide = True
        else:
            hide = fenced or stripped.startswith(">")
        if hide:
            for i in range(line.start(), line.end()):
                if masked[i] != "\n":
                    masked[i] = " "
    closers = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"', "`": "`"}
    closing = None
    for i, char in enumerate(masked):
        if closing is not None:
            if char == closing:
                closing = None
            if char != "\n":
                masked[i] = " "
        elif char in closers:
            closing = closers[char]
            masked[i] = " "
    for match in re.finditer(r"[^。！？!?；;\n]+[。！？!?；;]?", "".join(masked)):
        sentence = match.group()
        assertive = not _NONASSERTIVE.search(sentence) and not sentence.rstrip().endswith(("？", "?"))
        yield match.start(), match.end(), sentence, assertive


def _quote(observation, start, end, role):
    span = observation.provenance
    raw_start = next(e.char_start for e in observation.field_evidence if e.field == "raw_text")
    return EvidenceQuote(span, raw_start + start, raw_start + end,
                         observation.raw_text[start:end], role)


def _keys(observation):
    result = []
    for start, _, sentence, assertive in _sentences(observation.raw_text):
        if not assertive:
            continue
        for match in _OWN_KEY.finditer(sentence):
            prefix = re.split(r"[，,；;]", sentence[:match.start()])[-1]
            if re.search(r"\.(?:md|txt)\b|不是|并非|不属于|引用|对应|同一|不同于", prefix, re.I):
                continue
            if not any(char.isdigit() for char in match.group("key")):
                continue
            result.append((match.group("key"), _quote(observation, start + match.start(), start + match.end(), "TASK_KEY")))
    return result


def _namespaces(observation):
    # Scope labels may precede the narrower task excerpt in a Markdown span.
    # Multiple/quoted/unresolved labels are ambiguous, never a silent default.
    span = observation.provenance
    result = []
    recognized = set()
    for start, _, sentence, assertive in _sentences(span.quote):
        if assertive:
            for match in _NAMESPACE.finditer(sentence):
                offset = start + match.start()
                recognized.add(offset)
                result.append((match.group("name"), EvidenceQuote(span, span.char_start + offset,
                    span.char_start + start + match.end(), span.quote[offset:start + match.end()], "TASK_NAMESPACE")))
    for match in _NAMESPACE.finditer(span.quote):
        if match.start() not in recognized:
            result.append((None, EvidenceQuote(span, span.char_start + match.start(), span.char_start + match.end(),
                                                match.group(), "TASK_NAMESPACE")))
    return result


def _scope_quotes(observation):
    return [EvidenceQuote(observation.provenance, e.char_start, e.char_end, e.quote, "PROJECT_SCOPE")
            for e in observation.field_evidence if e.field == "project"]


def _aliases(artifact):
    names = {artifact.artifact_id.replace("\\", "/")}
    if artifact.source_uri:
        names.add(urlsplit(artifact.source_uri).path.replace("\\", "/"))
    return {alias for name in names for alias in (name, PurePosixPath(name).name) if alias}


def _same_project(a, b):
    return a.project is not None and b.project is not None and (
        normalize_temporal_component(a.project) == normalize_temporal_component(b.project))


def _asserted_reuse(sentence):
    for mention in _REUSE.finditer(sentence):
        for action in re.finditer(r"复用|重复|冲突|无法唯一指代", mention.group()):
            prefix = re.split(r"[，,；;]", sentence[:mention.start() + action.start()])[-1]
            prefix = re.sub(r"(?:任务)?编号|工单号|任务号", "", prefix)
            if not re.search(r"(?:没有|未曾|并未|从未|未|无|不是|并非|不|不存在)\s*(?:发生|出现|被)?\s*$", prefix):
                return True
    return False


def _references(subject, target, artifacts, inventory):
    """Resolve only self→named-source statements, never a free keyword co-occurrence."""
    supports, counters, contexts, reasons = [], [], [], set()
    target_sources = [a for a in artifacts if a.workspace_id == target.provenance.workspace_id
                      and a.artifact_id == target.provenance.artifact_id
                      and a.artifact_version_id == target.provenance.artifact_version_id]
    if not target_sources:
        return supports, counters, contexts, reasons
    for start, end, sentence, assertive in _sentences(subject.raw_text):
        for alias in sorted(_aliases(target_sources[0]), key=lambda name: (-len(name), name)):
            pattern = re.compile(r"(?<![A-Za-z0-9_./\\-])" + re.escape(alias) + r"(?![A-Za-z0-9_./\\-])")
            for mention in pattern.finditer(sentence):
                left, right = sentence[:mention.start()], sentence[mention.end():]
                clauses = re.split(r"[，,；;]", left)
                head = clauses[-1].strip()
                if not _SELF.search(left):
                    continue
                if not assertive and _SAME.search(right):
                    contexts.append(_quote(subject, start, end, "UNRESOLVED_REFERENCE"))
                    reasons.add("NONASSERTIVE_CONTEXT")
                    continue
                token = re.match(r"\s*(?:的)?\s*(?:任务\s*)?(?P<key>" + _KEY_TEXT + r")(?![A-Za-z0-9_-])", right)
                tail = right[token.end():] if token else re.sub(r"^\s*的", "", right, count=1)
                # A predicate must immediately qualify this reference. Searching the
                # rest of a sentence would attach C.md's 'same/different' to A.md.
                own_head = bool(_SELF_HEAD.fullmatch(head))
                prior_self = len(clauses) > 1 and bool(_SELF.match(clauses[-2].strip()))
                negative_head = bool(_NEG_HEAD.fullmatch(head) and (_SELF.match(head) or prior_self))
                negative_tail = bool(own_head and re.match(r"\s*[，,]?\s*" + _NEG_SAME.pattern, tail))
                positive = bool(own_head and re.match(r"\s*[，,]?\s*(?:是|为|属于)?\s*" + _SAME.pattern, tail))
                if not (negative_head or negative_tail or positive):
                    continue
                if not assertive:
                    contexts.append(_quote(subject, start, end, "UNRESOLVED_REFERENCE"))
                    reasons.add("NONASSERTIVE_CONTEXT")
                    continue
                matching_sources = {(a.artifact_id, a.artifact_version_id) for a in artifacts
                                    if a.workspace_id == target.provenance.workspace_id and alias in _aliases(a)}
                keys = _keys(target)
                target_inventory = [o for o in inventory if o.provenance.artifact_id == target.provenance.artifact_id
                                    and o.provenance.artifact_version_id == target.provenance.artifact_version_id]
                if token:
                    matching_observations = {o.observation_id for o in target_inventory
                                             if token.group("key") in {value for value, _ in _keys(o)}}
                    resolved = token.group("key") in {value for value, _ in keys} and matching_observations == {target.observation_id}
                else:
                    # Without a key, a partial excerpt or a multi-message source
                    # cannot establish that omitted competing tasks are absent.
                    source_keys = {m.group("key") for _, _, s, ok in _sentences(target.provenance.quote)
                                   if ok for m in _OWN_KEY.finditer(s)}
                    resolved = (bool(_SAME.search(right)) and len({o.observation_id for o in target_inventory}) == 1
                                and len(parse_source_text(target_sources[0]).segments) == 1
                                and target.raw_text.strip() == target.provenance.quote.strip()
                                and len(source_keys) <= 1 and len({value for value, _ in keys}) <= 1)
                if len(matching_sources) != 1 or not resolved:
                    contexts.append(_quote(subject, start, end, "AMBIGUITY"))
                    reasons.add("AMBIGUOUS_TASK_REFERENCE")
                    continue
                proof = _quote(subject, start, end, "IDENTITY_COUNTER" if negative_head or negative_tail else "IDENTITY_ASSERTION")
                (counters if negative_head or negative_tail else supports).append(proof)
    return supports, counters, contexts, reasons


def verify_relation_inputs(endpoints, artifacts, source_registry, context_observations=()):
    """Shared M1/P1 source, observation and registry checks; no I/O or mutation."""
    errors, dependencies = set(), []
    if any(o is None for o in endpoints):
        errors.add("INVALID_INPUT_CONTRACT")
    if not isinstance(artifacts, (tuple, list)) or any(not isinstance(a, RawArtifact) for a in artifacts):
        artifacts = ()
        errors.add("INVALID_INPUT_CONTRACT")
    if not isinstance(context_observations, (tuple, list)) or any(not isinstance(o, TaskObservation) for o in context_observations):
        context_observations = ()
        errors.add("INVALID_INPUT_CONTRACT")
    inventory = [o for o in (*endpoints, *context_observations) if o is not None]
    if len({o.provenance.workspace_id for o in inventory}) > 1:
        errors.add("CROSS_WORKSPACE")
    by_id = {}
    unique = {}
    for observation in inventory:
        reference = ObservationRef.from_observation(observation)
        by_id.setdefault(reference.observation_id, set()).add(reference.payload_hash)
        unique[(reference.observation_id, reference.payload_hash)] = observation
    if any(len(hashes) > 1 for hashes in by_id.values()):
        errors.add("EXTRACTION_CONFLICT")
    inventory = list(unique.values())
    for observation in inventory:
        span = observation.provenance
        matches = [a for a in artifacts if (a.workspace_id, a.artifact_id, a.artifact_version_id) ==
                   (span.workspace_id, span.artifact_id, span.artifact_version_id)]
        artifact = matches[0] if matches else None
        readback = artifact is not None and span.verify(artifact)
        valid = bool(readback and observation.verify(artifact) and observation.extraction_status == "EXTRACTED"
                     and observation.validation_status in {"VALID", "PARTIAL"} and observation.description is not None)
        if not readback:
            status = "INVALID"
            errors.add("SOURCE_VERSION_OR_HASH_MISMATCH" if any(a.artifact_id == span.artifact_id for a in artifacts)
                       else "SOURCE_MISSING")
        elif not isinstance(source_registry, SourceReferenceRegistry) or not source_registry.has_evidence(span.workspace_id, span.evidence_ref):
            status = "MISSING"
            errors.add("SOURCE_NOT_REGISTERED")
        elif (source_registry.evidence_hash(span.workspace_id, span.evidence_ref) != span.content_hash
              or source_registry.evidence_version(span.workspace_id, span.evidence_ref) != span.artifact_version_id):
            status = "NOT_CURRENT"
            errors.add("STALE_EVIDENCE")
        else:
            status = "CURRENT"
        if not valid:
            errors.add("OBSERVATION_VERIFICATION_FAILED")
        dependencies.append(SourceDependency(ObservationRef.from_observation(observation), span, status, bool(readback), valid))
    return errors, dependencies, inventory, artifacts


def verify_evidence_quote(quote, workspace_id, artifacts, source_registry):
    """Verify non-task source dependencies with the same immutable Source contract."""
    if not isinstance(quote, EvidenceQuote):
        return {"INVALID_EVIDENCE_CONTRACT"}, None
    span = quote.span
    errors = set()
    if span.workspace_id != workspace_id:
        errors.add("CROSS_WORKSPACE")
    matches = [a for a in artifacts if (a.workspace_id, a.artifact_id, a.artifact_version_id) ==
               (span.workspace_id, span.artifact_id, span.artifact_version_id)]
    readback = len(matches) == 1 and quote.verify(matches[0])
    if readback:
        canonical = any(span == segment.evidence_span for segment in parse_source_text(matches[0]).segments)
        full_context = span.ordinal == 0 and span.char_start == 0 and span.char_end == len(matches[0].content)
        if not (canonical or full_context):
            errors.add("NONCANONICAL_SOURCE_CONTEXT")
            readback = False
    if not readback:
        status = "INVALID"
        errors.add("SOURCE_VERSION_OR_HASH_MISMATCH")
    elif not isinstance(source_registry, SourceReferenceRegistry) or not source_registry.has_evidence(span.workspace_id, span.evidence_ref):
        status = "MISSING"
        errors.add("SOURCE_NOT_REGISTERED")
    elif (source_registry.evidence_hash(span.workspace_id, span.evidence_ref) != span.content_hash
          or source_registry.evidence_version(span.workspace_id, span.evidence_ref) != span.artifact_version_id):
        status = "NOT_CURRENT"
        errors.add("STALE_EVIDENCE")
    else:
        status = "CURRENT"
    return errors, SourceDependency(None, span, status, bool(readback), bool(readback))


def evidence_clauses(proposals):
    """Read complete sentence context; proposals cannot erase surrounding modality."""
    for proposal in proposals:
        span = proposal.span
        for start, end, masked, assertive in _sentences(span.quote):
            if span.char_start + start < proposal.char_end and span.char_start + end > proposal.char_start:
                yield proposal, start, end, masked, assertive


def proof_quote(proposal, start, end, role):
    span = proposal.span
    return EvidenceQuote(span, span.char_start + start, span.char_start + end, span.quote[start:end], role)


def observation_raw_range(observation):
    field = next(e for e in observation.field_evidence if e.field == 'raw_text')
    return field.char_start, field.char_end


def assess_same_task(subject: TaskObservation, object: TaskObservation, *, artifacts: Sequence[RawArtifact],
                     source_registry: SourceReferenceRegistry,
                     context_observations: Sequence[TaskObservation] = (),
                     assessed_at: datetime | None = None) -> RelationAssessment:
    """Fixed-pair identity assessment; include known competing observations explicitly."""
    endpoints = [subject if isinstance(subject, TaskObservation) else None,
                 object if isinstance(object, TaskObservation) else None]
    errors, dependencies, inventory, artifacts = verify_relation_inputs(
        endpoints, artifacts, source_registry, context_observations)

    def finish(verdict, reasons, support=(), counter=(), context=(), missing=(), direction=None):
        ordered = list(endpoints)
        if direction is not None:
            ordered = [direction, endpoints[1] if direction is endpoints[0] else endpoints[0]]
        elif all(o is not None for o in ordered):
            ordered.sort(key=lambda o: o.observation_id, reverse=True)
        return RelationAssessment(
            subject=ObservationRef.from_observation(ordered[0]) if ordered[0] else None,
            object=ObservationRef.from_observation(ordered[1]) if ordered[1] else None,
            observations=tuple(ordered), verdict=verdict, reason_codes=tuple(reasons), dependencies=tuple(dependencies),
            supporting_evidence=tuple(support), counter_evidence=tuple(counter), context_evidence=tuple(context),
            missing_evidence=tuple(missing), **({"assessed_at": assessed_at} if assessed_at is not None else {}))

    if errors:
        return finish("NOT_ASSESSABLE", errors, missing=("需要合法且已核验的同工作区观察、精确来源版本及当前来源目录。",))
    support, counter, context, reasons, directions = [], [], [], set(), []
    for origin, target in ((subject, object), (object, subject)):
        yes, no, uncertain, codes = _references(origin, target, artifacts, inventory)
        support.extend(yes)
        counter.extend(no)
        context.extend(uncertain)
        reasons.update(codes)
        if yes or no:
            directions.append(origin)
    direction = directions[0] if len({o.observation_id for o in directions}) == 1 else None
    keys = [_keys(o) for o in (subject, object)]
    namespaces = [_namespaces(o) for o in (subject, object)]
    shared = {k for k, _ in keys[0]} & {k for k, _ in keys[1]}
    scopes = [e for o in (subject, object) for e in _scope_quotes(o)] + [e for items in namespaces for _, e in items]
    key_proof = [e for items in keys for _, e in items]
    scope_conflict = (subject.project is not None and object.project is not None and not _same_project(subject, object))
    namespace_values = [{value for value, _ in items} for items in namespaces]
    unbound_namespace = False
    for observation in (subject, object):
        span = observation.provenance
        artifact = next(a for a in artifacts if a.workspace_id == span.workspace_id
                        and a.artifact_version_id == span.artifact_version_id)
        if any(m.start() < span.char_start or m.end() > span.char_end for m in _NAMESPACE.finditer(artifact.content)):
            # A label in another message is a scope gap, not permission to
            # silently attach that message's namespace to this task.
            unbound_namespace = True
    namespace_uncertain = unbound_namespace or any(None in values for values in namespace_values)
    namespace_conflict = (namespace_values[0] != namespace_values[1] or namespace_uncertain
                          or any(len(values) > 1 for values in namespace_values))
    ambiguity = any(len({k for k, _ in items}) > 1 for items in keys)
    for observation in inventory:
        if not _same_project(observation, subject):
            continue
        if shared & {k for k, _ in _keys(observation)}:
            for start, end, sentence, assertive in _sentences(observation.raw_text):
                if assertive and _asserted_reuse(sentence):
                    ambiguity = True
                    context.append(_quote(observation, start, end, "AMBIGUITY"))
    if support and counter:
        return finish("CONFLICT", reasons | {"CONFLICTING_IDENTITY_EVIDENCE"}, support + scopes + key_proof,
                      counter, context, ("需要对相互矛盾的身份声明作明确纠错或撤销。",), direction)
    if counter:
        return finish("REFUTED", reasons | {"EXPLICIT_DIFFERENT_TASK"}, counter=counter + scopes + key_proof,
                      context=context, direction=direction)
    if scope_conflict:
        reasons.add("PROJECT_SCOPE_MISMATCH")
    if namespace_conflict:
        reasons.add("TASK_NAMESPACE_UNRESOLVED" if namespace_uncertain else "TASK_NAMESPACE_MISMATCH")
    if ambiguity:
        reasons.add("AMBIGUOUS_TASK_KEY")
    blocked = scope_conflict or namespace_conflict or ambiguity or "AMBIGUOUS_TASK_REFERENCE" in reasons
    if support and not blocked:
        return finish("SUPPORTED", reasons | {"EXPLICIT_TASK_REFERENCE"}, support + scopes + key_proof,
                      context=context, direction=direction)
    if shared and _same_project(subject, object) and not blocked and "NONASSERTIVE_CONTEXT" not in reasons:
        return finish("SUPPORTED", reasons | {"SCOPED_TASK_KEY"}, key_proof + scopes, context=context)
    if subject.project is None or object.project is None:
        reasons.add("PROJECT_SCOPE_UNKNOWN")
    return finish("UNKNOWN", reasons | {"INSUFFICIENT_IDENTITY"}, context=context + support + scopes + key_proof,
                  missing=("需要作用域明确且无冲突的任务编号，或唯一指向另一观察的明确同一任务声明；不会补写项目、负责人或日期。",))
