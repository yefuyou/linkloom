"""Bounded P2 proof validation. No discovery, task mutation, transitivity or writes.

Proposals select source ranges, not verdicts. Rules inspect their full sentence
context so a true substring cannot erase a condition, quotation or negation.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

from linkloom.semantic_ingestion.artifacts import EvidenceSpan, evidence_ref_for, line_range_for, message_id_for, segment_id_for
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import (
    ChangeRef, EvidenceQuote, ObservationRef, ProjectRef, RelationAssessment,
)
from linkloom.semantic_ingestion.task_relation_validation import (
    _sentences, _NONASSERTIVE, verify_evidence_quote, verify_relation_inputs,
    evidence_clauses as _clauses, proof_quote as _proof, observation_raw_range as _raw_range,
)
from linkloom.semantic_ingestion.task_work_proofs import impact_proofs, related_proofs, change_claim_proofs


def source_context_quote(artifact):
    """Explicit full-source proposal for headings outside M1 speech segments.

    The caller must register this EvidenceSpan in its source snapshot. This pure
    helper never edits the registry or the original M1 observation/segment.
    """
    text = artifact.content
    digest = hashlib.sha256(text.encode('utf8')).hexdigest()
    message = message_id_for(artifact.artifact_version_id, 0)
    segment = segment_id_for(artifact.artifact_version_id, message, 0, len(text), digest)
    first, last = line_range_for(text, 0, len(text))
    span = EvidenceSpan(artifact.workspace_id, artifact.artifact_id, artifact.artifact_version_id,
        artifact.source_ref, artifact.content_hash, 0, message, segment, evidence_ref_for(segment),
        0, len(text), first, last, text, digest)
    return EvidenceQuote(span, 0, len(text), text, 'RELATION_PROPOSAL')


def _direct_project(observation, project, proposals):
    supports = []
    lo, hi = _raw_range(observation)
    for proposal, start, end, sentence, ok in _clauses(proposals):
        if proposal.span.artifact_version_id != observation.provenance.artifact_version_id or not ok:
            continue
        if not (lo <= proposal.span.char_start + start < hi):
            continue
        # Explicit ownership of named-project work, not a name somewhere nearby.
        if re.match(r'\s*我(?:继续)?负责\s*' + re.escape(project.name) + r'(?:的|任务|\s)', sentence):
            supports.append(_proof(proposal, start, end, 'PROJECT_BINDING'))
    return supports


def _alias_proof(alias, project, proposals):
    yes, no, scopes = [], [], set()
    # Literal quotation marks around names are allowed only inside this exact
    # naming grammar, not around the entire assertion or in reported speech.
    naming = (r'\s*(?:项目说明[：:]\s*)?本工作区的[“「]?'+ re.escape(alias.name) + r'[”」]?'
              r'(?P<verb>是|并非|不是)[“「]?' + re.escape(project.name) + r'[”」]?的内部代号'
              r'(?:，两者在本期(?P<scope>[^，。；\n]+?)资料中指同一项目)?[。]?\s*')
    for proposal in proposals:
        for start, end, masked, _ in _sentences(proposal.span.quote):
            raw = proposal.span.quote[start:end]
            if proposal.span.char_start + start >= proposal.char_end or proposal.span.char_start + end <= proposal.char_start:
                continue
            match = re.fullmatch(naming, raw)
            # A masked whole assertion (quote, blockquote, fence) cannot become
            # an assertion by matching the original string.
            if not match or not masked.lstrip().startswith(('项目说明', '本工作区')):
                continue
            quote = _proof(proposal, start, end, 'PROJECT_ALIAS')
            (yes if match['verb'] == '是' else no).append(quote)
            scopes.add(match['scope'] or 'WORKSPACE')
    return yes, no, scopes


def _heading_scope(observation, proposals):
    lo, _ = _raw_range(observation)
    result = []

    def submitted(span, start, end):
        absolute_start, absolute_end = span.char_start + start, span.char_start + end
        for candidate in proposals:
            if (candidate.span.artifact_version_id == span.artifact_version_id
                and candidate.char_start <= absolute_start and candidate.char_end >= absolute_end):
                return EvidenceQuote(candidate.span, absolute_start, absolute_end,
                                     span.quote[start:end], 'PROJECT_BINDING')
        return None

    for proposal in proposals:
        if proposal.span.artifact_version_id != observation.provenance.artifact_version_id:
            continue
        span = proposal.span
        visible = list(' ' * len(span.quote))
        for start, end, masked, _ in _sentences(span.quote):
            visible[start:end] = masked
        visible = ''.join(visible)
        # Preserve offsets using the original line positions, but require that
        # the heading survives the common quoted/code context masker.
        headings = [m for m in re.finditer(r'^(#{1,6})[ \t]+([^\r\n]+)', span.quote, re.M)
                    if visible[m.start():m.end()] == m.group()]
        prior = [h for h in headings if span.char_start + h.end() < lo]
        for heading in prior:
            following = next((h.start() for h in headings if h.start() > heading.start()
                              and len(h[1]) <= len(heading[1])), len(span.quote))
            if lo >= span.char_start + following:
                continue
            name = heading[2].strip()
            title = submitted(span, heading.start(), heading.end())
            if not title or not name or _NONASSERTIVE.search(name):
                continue
            # A parent scope survives subheadings. Its declaration is in its
            # own preamble; a child's declaration is evaluated separately.
            preamble_end = next((h.start() for h in headings if h.start() > heading.start()), lo - span.char_start)
            section_end = min(preamble_end, lo - span.char_start)
            section = span.quote[heading.end():section_end]
            declarations, quotes, missing = [], [title], False
            for start, end, sentence, asserted in _sentences(section):
                declaration = re.fullmatch(r'\s*本节(?P<theme>[^。；\n]*?)任务仅属于(?P<name>[^。；\s]+)[。]?[ \t]*', sentence)
                if declaration and asserted:
                    quote = submitted(span, heading.end() + start, heading.end() + end)
                    if quote is None:
                        missing = True
                    else:
                        declarations.append(declaration)
                        quotes.append(quote)
            if missing:
                continue
            same = [m for m in declarations if m['name'] == name]
            result.append((name, bool(same), same[0]['theme'] if same else '', tuple(quotes)))
            result.extend((m['name'], True, m['theme'], tuple(quotes)) for m in declarations if m['name'] != name)
    return result


def _cross_document_project(observation, project, proposals, artifacts, inventory):
    from linkloom.semantic_ingestion.task_work_proofs import unique_source
    supports = []
    local = [o for o in inventory if o.provenance.artifact_version_id == observation.provenance.artifact_version_id]
    if len({o.observation_id for o in local}) != 1:
        return supports
    source = next(a for a in artifacts if a.artifact_version_id == observation.provenance.artifact_version_id)
    # This finite document-wide '本任务' grammar is only defined for one visibly
    # stated task line. More tasks require a task-key anchor in a later slice.
    task_lines = [line for line in source.content.splitlines() if '任务' in line and '计划' in line]
    if len(task_lines) != 1 or observation.raw_text not in task_lines[0]:
        return supports
    for proposal, start, end, sentence, ok in _clauses(proposals):
        if proposal.span.artifact_version_id != observation.provenance.artifact_version_id or not ok:
            continue
        match = re.fullmatch(r'\s*(?:(?:项目|任务)说明[：:]\s*)?本任务所属项目为\s*(?P<file>\S+\.(?:md|txt))\s*登记的' + re.escape(project.name) + r'[。]?\s*', sentence)
        if not match:
            continue
        for anchor, astart, aend, asentence, aok in _clauses(proposals):
            if not aok or not unique_source(match['file'], anchor.span, artifacts):
                continue
            if re.fullmatch(r'\s*项目登记[：:]本工作区的[^。；\n]+(?:工作属于|事项归属)' + re.escape(project.name) + r'[。]?\s*', asentence):
                supports.extend([_proof(proposal, start, end, 'PROJECT_BINDING'),
                                 _proof(anchor, astart, aend, 'PROJECT_BINDING')])
    return supports


def assess_relation(predicate, subject, object, *, artifacts, source_registry, evidence=(),
                    context_observations=(), alias_assessments=(), decision_store=None,
                    assessed_at: datetime | None = None):
    """Assess fixed typed endpoints using finite, untrusted source proposals.

    Use assess_same_task for P1. P2 never implements REVISES_PLAN/date comparison.
    """
    shapes = {'IN_PROJECT': (TaskObservation, ProjectRef), 'PROJECT_ALIAS': (ProjectRef, ProjectRef),
              'RELATED_TO': (TaskObservation, TaskObservation), 'AFFECTED_BY': (TaskObservation, ChangeRef)}
    if predicate not in shapes:
        raise ValueError('predicate outside P2')
    if not isinstance(subject, shapes[predicate][0]) or not isinstance(object, shapes[predicate][1]):
        refs, obs = [], []
        for value, kind in zip((subject, object), shapes[predicate]):
            valid = isinstance(value, kind)
            refs.append(ObservationRef.from_observation(value) if valid and isinstance(value, TaskObservation) else value if valid else None)
            obs.append(value if valid and isinstance(value, TaskObservation) else None)
        return RelationAssessment(*refs, 'NOT_ASSESSABLE', ('INVALID_INPUT_CONTRACT',), tuple(obs), (), predicate=predicate)
    workspace = subject.provenance.workspace_id if isinstance(subject, TaskObservation) else subject.workspace_id
    other_workspace = object.provenance.workspace_id if isinstance(object, TaskObservation) else object.workspace_id
    observations = tuple(o for o in (subject, object) if isinstance(o, TaskObservation))
    errors, dependencies, inventory, artifacts = verify_relation_inputs(
        observations, artifacts, source_registry, context_observations)
    if workspace != other_workspace:
        errors.add('CROSS_WORKSPACE')
    if not isinstance(evidence, (tuple, list)) or any(not isinstance(e, EvidenceQuote) for e in evidence):
        errors.add('INVALID_EVIDENCE_CONTRACT')
        evidence = ()
    for quote in evidence:
        codes, dependency = verify_evidence_quote(quote, workspace, artifacts, source_registry)
        errors.update(codes)
        if dependency:
            dependencies.append(dependency)
    if not dependencies:
        errors.add('SOURCE_CONTEXT_REQUIRED')
    if isinstance(object, ChangeRef):
        codes, dependency = verify_evidence_quote(object.evidence, workspace, artifacts, source_registry)
        errors.update(codes)
        if dependency:
            dependencies.append(dependency)
    subject_ref = ObservationRef.from_observation(subject) if isinstance(subject, TaskObservation) else subject
    object_ref = ObservationRef.from_observation(object) if isinstance(object, TaskObservation) else object

    def finish(verdict, reasons, support=(), counter=(), missing=(), detail=None, premises=(), authority=()):
        return RelationAssessment(subject_ref, object_ref, verdict, tuple(reasons),
            tuple(o if isinstance(o, TaskObservation) else None for o in (subject, object)),
            tuple(dependencies), tuple(support), tuple(counter), (), tuple(missing),
            **({'assessed_at': assessed_at} if assessed_at else {}), predicate=predicate,
            relation_detail=detail, premise_ids=tuple(premises), authority_checks=tuple(authority))

    if errors:
        return finish('NOT_ASSESSABLE', errors, missing=('需要同工作区、可读且当前的精确来源和合法观察。',))
    if predicate == 'PROJECT_ALIAS':
        yes, no, scopes = _alias_proof(subject, object, evidence)
        if len(scopes) > 1:
            return finish('UNKNOWN', ('AMBIGUOUS_ALIAS_SCOPE',), missing=('需要唯一的别名适用资料范围。',))
        if yes or no:
            return finish('CONFLICT' if yes and no else 'SUPPORTED' if yes else 'REFUTED',
                          ('EXPLICIT_PROJECT_ALIAS',), yes, no, detail='ALIAS_SCOPE:' + next(iter(scopes)))
    if predicate == 'IN_PROJECT':
        support = _direct_project(subject, object, evidence) + _cross_document_project(subject, object, evidence, artifacts, inventory)
        counter, premises = [], []
        validated_aliases = []
        for alias in alias_assessments:
            if not isinstance(alias, RelationAssessment) or alias.predicate != 'PROJECT_ALIAS':
                return finish('NOT_ASSESSABLE', ('INVALID_ALIAS_PREMISE',))
            replay = assess_relation('PROJECT_ALIAS', alias.subject, alias.object, artifacts=artifacts,
                source_registry=source_registry, evidence=[*alias.supporting_evidence, *alias.counter_evidence])
            dependencies.extend(replay.dependencies)
            if alias.subject.workspace_id != workspace or replay.verdict == 'NOT_ASSESSABLE':
                return finish('NOT_ASSESSABLE', ('INVALID_OR_STALE_ALIAS_PREMISE',))
            if replay.verdict == 'SUPPORTED' and alias.verdict == 'SUPPORTED' and replay.relation_detail == alias.relation_detail:
                validated_aliases.append(alias)
        for name, exclusive, theme, heading in _heading_scope(subject, evidence):
            if name == object.name and (exclusive or name.endswith('项目')):
                support.extend(heading)
            else:
                candidates = [a for a in validated_aliases if a.subject.name == name
                              and (a.relation_detail == 'ALIAS_SCOPE:WORKSPACE' or a.relation_detail == 'ALIAS_SCOPE:' + theme)]
                destinations = {a.object.name for a in candidates}
                if destinations == {object.name}:
                    for alias in candidates:
                        support.extend([*heading, *alias.supporting_evidence])
                        premises.append(alias.assessment_id)
                elif exclusive and not candidates and name.endswith('项目'):
                    counter.extend(heading)
        if support or counter:
            return finish('CONFLICT' if support and counter else 'SUPPORTED' if support else 'REFUTED',
                ('EXPLICIT_PROJECT_SCOPE',), support, counter, premises=sorted(set(premises)))
    if predicate == 'RELATED_TO':
        yes, no = related_proofs(subject, object, evidence, artifacts, inventory)
        if yes or no:
            return finish('CONFLICT' if yes and no else 'SUPPORTED' if yes else 'REFUTED',
                          ('EXPLICIT_WORK_LINK',), yes, no, detail='WORK_LINK')
    if predicate == 'AFFECTED_BY':
        if object.kind == 'SOURCE_CLAIM':
            authority = ('SOURCE_CLAIM_ONLY', 'FORMAL_AUTHORITY_NOT_ESTABLISHED')
        else:
            from linkloom.semantic_ingestion.task_change_authority import verify_change_authority
            codes, authority, extra = verify_change_authority(object, decision_store, source_registry, artifacts)
            for quote in extra:
                more, dependency = verify_evidence_quote(quote, workspace, artifacts, source_registry)
                codes.update(more)
                if dependency:
                    dependencies.append(dependency)
            if codes:
                return finish('NOT_ASSESSABLE', codes, authority=authority,
                              missing=('需要真实且当前有效的审核回执、候选映射与供应商替代链。',))
        change_proof = change_claim_proofs(object)
        yes, no = impact_proofs(subject, object, evidence, artifacts)
        if change_proof and (yes or no):
            return finish('CONFLICT' if yes and no else 'SUPPORTED' if yes else 'REFUTED',
                ('EXPLICIT_DIRECT_CHANGE_IMPACT',), [*yes, *change_proof] if yes else (),
                [*no, *change_proof] if no else (), detail='DIRECT_ADAPTATION', authority=authority)
        return finish('UNKNOWN', ('NO_UNIQUE_DIRECT_CHANGE_BRIDGE',), authority=authority,
            missing=('需要将本任务明确连接到这一来源的具体供应商变更；供应商、时间及项目共现不足。',))
    return finish('UNKNOWN', ('INSUFFICIENT_RELATION_EVIDENCE',),
                  missing=('需要唯一绑定端点、作用域及该关系命题的明确原文证据。',))
