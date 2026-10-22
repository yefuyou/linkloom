"""Evidence-bound business plan revisions. Source edits are separate facts.

Finite Chinese declarations only; no timestamp ordering, similarity or date
differences establish a revision. Callers provide fixed endpoints and proposals.
"""
from __future__ import annotations

from datetime import date, datetime
import re

from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, ObservationRef, RelationAssessment
from linkloom.semantic_ingestion.task_relation_validation import (
    _aliases, _keys, _references, _sentences, _NONASSERTIVE, observation_raw_range,
    assess_same_task, verify_evidence_quote, verify_relation_inputs, proof_quote,
)
from linkloom.semantic_ingestion.task_work_proofs import unique_source
from linkloom.semantic_ingestion.document_text import parse_source_text


def revalidate_identity(identity, new, old, artifacts, registry, context=()):
    """Recompute the P1 proof, not merely its caller-supplied verdict or hash."""
    if not isinstance(identity, RelationAssessment) or identity.predicate != 'SAME_TASK':
        return {'INVALID_IDENTITY_PREMISE'}, None
    try:
        RelationAssessment.from_dict(identity.to_dict())
    except (ValueError, TypeError, AttributeError):
        return {'INVALID_IDENTITY_PREMISE'}, None
    replay = assess_same_task(new, old, artifacts=artifacts, source_registry=registry,
                              context_observations=context)
    errors = set()
    if replay.verdict == 'NOT_ASSESSABLE':
        errors.update(replay.reason_codes)
    if identity.assessment_id != replay.assessment_id:
        errors.add('IDENTITY_PREMISE_REPLAY_MISMATCH')
    return errors, replay


def plan_statement_assertive(sentence,base):
    return base or ('仅在原任务范围内增加' in sentence and not _NONASSERTIVE.search(
        sentence.replace('仅在原任务范围内增加','在原任务范围内增加')))


def _statements(proposals):
    """A submitted range must contain a complete asserted statement."""
    seen = set()
    for proposal in proposals:
        span = proposal.span
        for start, end, sentence, ok in _sentences(span.quote):
            absolute_start, absolute_end = span.char_start + start, span.char_start + end
            if not (proposal.char_start <= absolute_start and proposal.char_end >= absolute_end):
                continue
            key = (span.artifact_version_id, absolute_start, absolute_end)
            if key not in seen and plan_statement_assertive(sentence,ok):
                seen.add(key)
                yield proposal, start, end, sentence


def _own_statement(proposal, start, end, observation):
    lo, hi = observation_raw_range(observation)
    return (proposal.span.artifact_version_id == observation.provenance.artifact_version_id
            and lo <= proposal.span.char_start + start < hi and proposal.span.char_start + end <= hi)


def _unique_file_target(alias, target, artifacts, inventory):
    if not unique_source(alias, target.provenance, artifacts):
        return False
    artifact = next(a for a in artifacts if a.artifact_version_id == target.provenance.artifact_version_id)
    # Unnumbered file references cannot hide other observations or messages.
    peers = {o.observation_id for o in inventory if o.provenance.artifact_version_id == artifact.artifact_version_id}
    return (peers == {target.observation_id} and len(parse_source_text(artifact).segments) == 1
            and target.raw_text.strip() == target.provenance.quote.strip() and len(_keys(target)) <= 1)


def _self_plan_actions(sentence):
    """Only implicit self clauses or explicit 本条/本计划 own plan actions.

    A different subject before an action cannot borrow this observation's
    earlier identity declaration. Unknown subject wording stays unresolved.
    """
    actions=r'现修订|将原定|本条替代|本计划替代|不修订任务计划|任务计划未曾修订|不是业务计划修订|资料更正'
    return all(re.split('[，,]',sentence[:m.start()])[-1].strip() in {'','现','同时声明'}
               for m in re.finditer(actions,sentence))


def _positive_replacement(sentence):
    return re.search(r'(?:^|[，,])(?:现修订(?:其)?(?:截止)?计划|'
        r'(?:本计划(?:自\d{4}年\d{1,2}月\d{1,2}日起生效并)?|并)?替代原计划)(?=[，,。；;]|$)',sentence)


def _proofs(new, old, proposals, artifacts, inventory):
    yes, no, fields, direction_conflict = [], [], set(), False
    ref_yes, _, _, _ = _references(new, old, artifacts, inventory)
    reverse_yes, _, _, _ = _references(old, new, artifacts, inventory)
    for proposal, start, end, sentence in _statements(proposals):
        origin_new = _own_statement(proposal, start, end, new)
        origin_old = _own_statement(proposal, start, end, old)
        if not (origin_new or origin_old):
            continue
        origin, target, refs = (new, old, ref_yes) if origin_new else (old, new, reverse_yes)
        explicit = re.search(r'本条替代(?P<file>[^，,。；;\s]+\.(?:md|txt))计划', sentence)
        anchored = bool(refs) or bool(explicit and _unique_file_target(explicit['file'],target,artifacts,inventory))
        counter_anchor = False
        if origin_old:
            # A denial in the old plan must explicitly name the proposed newer
            # source and this old plan, not another file's unrelated revision.
            for artifact in artifacts:
                if artifact.artifact_version_id != new.provenance.artifact_version_id:
                    continue
                for alias in _aliases(artifact):
                    if _unique_file_target(alias,new,artifacts,inventory) and re.fullmatch(
                        re.escape(alias) + r'(?:的[^，。]+版本(?:是草稿，)?)?(?:不替代本条[^。；]*计划|未修订本条计划)[。；;]?', sentence.strip()):
                        counter_anchor = True
        if counter_anchor:
            no.append(proof_quote(proposal,start,end,'PLAN_COUNTER'))
            continue
        if not anchored or not _self_plan_actions(sentence):
            continue
        correction = re.search(r'(?:仅|只是|只做)(?:资料|来源|文本|文档)(?:版本)?纠错',sentence)
        denial = re.search(r'(?:不修订任务计划|任务计划未曾修订|业务计划未(?:曾)?改变|不改变业务计划|不是业务计划修订)',sentence)
        if denial and origin_new:
            no.append(proof_quote(proposal,start,end,'PLAN_COUNTER'))
        if correction or '修正录入错误' in sentence:
            # Text correction without an explicit business revision is never
            # positive proof, even if two written dates happen to differ.
            continue
        deadline_change = re.search(r'(?:现修订其计划，)?将原定(?P<old>[^，。；]+?)改为(?P<new>[^，。；]+?)(?:，|。|；|$)',sentence)
        scope_change = re.search(r'仅在原任务范围内增加[^，。；]+',sentence)
        boundary_change = re.search(r'现修订截止边界并替代原计划',sentence)
        # A whole positive clause is required. Looking behind one character
        # misses modal negatives such as 不应替代/不能替代.
        replacement = explicit or boundary_change or _positive_replacement(sentence)
        if replacement and (deadline_change or scope_change or boundary_change):
            if deadline_change:
                # The bridge must restate the old/new deadline tokens. A true
                # date in an unrelated role cannot bind these plan endpoints.
                if (target.deadline_text is None or origin.deadline_text is None
                    or target.deadline_text not in deadline_change['old'] or origin.deadline_text not in deadline_change['new']):
                    continue
                fields.add('deadline')
            if boundary_change and origin.deadline_text:
                fields.add('deadline')
            if scope_change:
                fields.add('scope')
            quote = proof_quote(proposal,start,end,'PLAN_REVISION')
            if origin_new:
                yes.append(quote)
            else:
                no.append(quote)
                direction_conflict = True
    # Two still-current, independently declared replacements of this same old
    # plan with incompatible deadlines are a branch, not permission to choose
    # the later source. This is bounded counter-evidence, not task clustering.
    if yes:
        for candidate in inventory:
            if candidate.observation_id in {new.observation_id,old.observation_id}:
                continue
            own_keys={k for k,_ in _keys(candidate)}
            if own_keys != {k for k,_ in _keys(old)} or not own_keys:
                continue
            for proposal,start,end,sentence in _statements(proposals):
                if not _own_statement(proposal,start,end,candidate):
                    continue
                match=re.search(r'本条(?:也)?是(?P<file>[^，。；\s]+\.(?:md|txt))的同一任务',sentence)
                if (not match or not _unique_file_target(match['file'],old,artifacts,inventory)
                    or not _positive_replacement(sentence) or not _self_plan_actions(sentence)):
                    continue
                change=re.search(r'将原定(?P<old>[^，。；]+?)改为(?P<new>[^，。；]+?)(?:，|。|；|$)',sentence)
                if (change and old.deadline_text and candidate.deadline_text
                    and old.deadline_text in change['old'] and candidate.deadline_text in change['new']
                    and candidate.deadline is not None and candidate.deadline != new.deadline):
                    no.append(proof_quote(proposal,start,end,'PLAN_COUNTER'))
    return yes, no, fields, direction_conflict


def assess_plan_revision(new, old, *, same_task, artifacts, source_registry, evidence=(),
                         context_observations=(), assessed_at: datetime | None = None):
    endpoints = tuple(o if isinstance(o,TaskObservation) else None for o in (new,old))
    errors, dependencies, inventory, artifacts = verify_relation_inputs(endpoints,artifacts,source_registry,context_observations)
    if not isinstance(evidence,(list,tuple)) or any(not isinstance(e,EvidenceQuote) for e in evidence):
        errors.add('INVALID_EVIDENCE_CONTRACT')
        evidence = ()
    if all(o is not None for o in endpoints):
        for quote in evidence:
            codes, dependency = verify_evidence_quote(quote,new.provenance.workspace_id,artifacts,source_registry)
            errors.update(codes)
            if dependency:
                dependencies.append(dependency)
    identity_errors, replay = revalidate_identity(same_task,*endpoints,artifacts,source_registry,context_observations)
    errors.update(identity_errors)
    if replay:
        dependencies.extend(replay.dependencies)

    def finish(verdict,reasons,yes=(),no=(),changed=(),effective=None,detail=None):
        return RelationAssessment(*(ObservationRef.from_observation(o) if o else None for o in endpoints),
            verdict,tuple(reasons),endpoints,tuple(dependencies),tuple(yes),tuple(no),
            missing_evidence=() if verdict in {'SUPPORTED','REFUTED'} else ('需要当前有效的同任务证明和唯一绑定新旧计划的明确修订/反证。',),
            predicate='REVISES_PLAN',changed_fields=tuple(changed) if verdict=='SUPPORTED' else (),asserted_effective_at=effective,
            relation_detail=detail,premise_ids=(same_task.assessment_id,) if isinstance(same_task,RelationAssessment) else (),
            **({'assessed_at':assessed_at} if assessed_at else {}))

    if errors:
        return finish('NOT_ASSESSABLE',errors)
    if replay.verdict != 'SUPPORTED':
        return finish('UNKNOWN',('SAME_TASK_NOT_SUPPORTED',))
    yes,no,changed,opposite = _proofs(new,old,evidence,artifacts,inventory)
    if yes and no:
        return finish('CONFLICT',('CONFLICTING_REVISION_EVIDENCE',),yes,no,changed)
    if opposite:
        return finish('UNKNOWN',('REVISION_DIRECTION_OPPOSED',),no=no)
    if no:
        return finish('REFUTED',('EXPLICIT_NO_BUSINESS_REVISION',),no=no,detail='NO_BUSINESS_PLAN_REVISION')
    if yes:
        effective=set()
        for proposal,start,end,sentence in _statements(evidence):
            if not _own_statement(proposal,start,end,new):
                continue
            for match in re.finditer(r'本计划自(\d{4})年(\d{1,2})月(\d{1,2})日起生效',sentence):
                try:
                    effective.add(date(*map(int,match.groups())).isoformat())
                except ValueError:
                    return finish('UNKNOWN',('INVALID_EXPLICIT_PLAN_EFFECTIVE_DATE',))
                yes.append(proof_quote(proposal,start,end,'PLAN_EFFECTIVE'))
        if len(effective)>1:
            return finish('CONFLICT',('CONFLICTING_EFFECTIVE_DATES',),yes,yes,changed)
        return finish('SUPPORTED',('EXPLICIT_PLAN_REPLACEMENT',),yes,changed=changed,
                      effective=next(iter(effective)) if effective else None,detail='BUSINESS_PLAN_REVISION')
    return finish('UNKNOWN',('NO_REVISION_EVIDENCE',))
