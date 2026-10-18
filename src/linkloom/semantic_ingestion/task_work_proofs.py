"""Finite work/causal proof grammar, independent of project/identity inference."""
from pathlib import PurePosixPath
import re

from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_relation_validation import (
    _aliases, _keys, _OWN_KEY, _sentences, _NONASSERTIVE,
    evidence_clauses, proof_quote, observation_raw_range,
)


def unique_source(name, endpoint, artifacts):
    matches = [a for a in artifacts if a.workspace_id == endpoint.workspace_id and name in _aliases(a)]
    return len(matches) == 1 and (matches[0].artifact_id, matches[0].artifact_version_id) == (
        endpoint.artifact_id, endpoint.artifact_version_id)


def _target(fragment, target, artifacts, inventory):
    fragment = fragment.strip()
    key = re.fullmatch(r'任务\s*(\S+)', fragment)
    artifact = next(a for a in artifacts if a.artifact_version_id == target.provenance.artifact_version_id)
    if key:
        value = key[1]
        source_keys = [m['key'] for _, _, sentence, ok in _sentences(artifact.content) if ok for m in _OWN_KEY.finditer(sentence)]
        return (value in {k for k, _ in _keys(target)} and source_keys.count(value) == 1
                and {o.observation_id for o in inventory if o.provenance.artifact_version_id == artifact.artifact_version_id
                     and value in {k for k, _ in _keys(o)}} == {target.observation_id})
    fragment = re.sub(r'工作$', '', fragment)
    # Unnumbered anchors require a unique exact work fragment in the full target
    # source, not a similarity score or an omitted competing observation.
    return len(fragment) >= 2 and fragment in target.raw_text and artifact.content.count(fragment) == 1


def related_proofs(subject, target, proposals, artifacts, inventory):
    yes, no = [], []
    lo, hi = observation_raw_range(subject)
    stem = PurePosixPath(subject.provenance.artifact_id).stem
    label = re.escape(stem + str(subject.provenance.ordinal + 1))
    for proposal, start, end, sentence, _ in evidence_clauses(proposals):
        if proposal.span.artifact_version_id != subject.provenance.artifact_version_id:
            continue
        # Only the work-link clause is asserted; D03's separate identity question
        # after the comma does not negate the explicit work relationship.
        clause = re.split('[，,]', sentence, maxsplit=1)[0].strip()
        if _NONASSERTIVE.search(clause):
            continue
        pattern = (r'(?:说明[：:]\s*)?(?P<origin>本任务|此任务|' + label + r'的(?P<work>[^与和]+))'
                   r'与(?P<file>[^\s，。]+?\.(?:md|txt))的(?P<target>[^，。]+?)'
                   r'(?P<link>有工作联系|无工作联系|工作相关|工作无关)[。；;]?')
        match = re.fullmatch(pattern, clause)
        if not match:
            continue
        within_raw = lo <= proposal.span.char_start + start < hi
        if match['work']:
            origin_ok = match['work'] in subject.raw_text
        else:
            origin_ok = within_raw
        if (not origin_ok or not unique_source(match['file'], target.provenance, artifacts)
            or not _target(match['target'], target, artifacts, inventory)):
            continue
        quote = proof_quote(proposal, start, start + len(re.split('[，,]', sentence, maxsplit=1)[0]), 'WORK_LINK')
        (no if match['link'] in {'无工作联系', '工作无关'} else yes).append(quote)
    return yes, no


def change_claim_proofs(change):
    yes = []
    for proposal, start, end, sentence, ok in evidence_clauses([change.evidence]):
        pattern = (re.escape(change.project) + r'(?:的)?(?:短信(?:服务)?)?供应商(?:由|从)'
                   + re.escape(change.before_vendor) + r'(?:改为|改用|改成)' + re.escape(change.after_vendor))
        if ok and re.match(r'\s*' + pattern, sentence):
            yes.append(proof_quote(proposal, start, end, 'CHANGE_CLAIM'))
    return yes


def impact_proofs(subject, change, proposals, artifacts):
    yes, no = [], []
    lo, hi = observation_raw_range(subject)
    for proposal, start, end, sentence, ok in evidence_clauses(proposals):
        if (not ok or proposal.span.artifact_version_id != subject.provenance.artifact_version_id
            or not lo <= proposal.span.char_start + start < hi):
            continue
        bridge = (r'(?P<file>[^\s，。]+?\.(?:md|txt))记载的从' + re.escape(change.before_vendor)
                  + r'改用' + re.escape(change.after_vendor))
        positive = re.fullmatch(r'\s*(?:因为|由于)' + bridge + r'[，,](?:此任务|本任务)必须(?P<action>调整|适配|补测|增加|等待)(?P<work>[^。]+)[。]?', sentence)
        negative = re.fullmatch(r'\s*(?:本任务|此任务)无需因' + bridge + r'做任何调整[。]?', sentence)
        match = positive or negative
        if match:
            if not unique_source(match['file'], change.evidence.span, artifacts):
                continue
            artifact = next(a for a in artifacts if a.artifact_version_id == change.evidence.span.artifact_version_id)
            transition = (r'供应商(?:由|从)' + re.escape(change.before_vendor)
                          + r'(?:改为|改用|改成)' + re.escape(change.after_vendor))
            if sum(len(re.findall(transition, s)) for _, _, s, asserted in _sentences(artifact.content) if asserted) != 1:
                continue
        else:
            # Explicit local demonstratives are supported only for one change
            # in the whole source and a task with an explicit matching project.
            # Adjacency without a causal demonstrative never reaches this rule.
            if subject.project != change.project or subject.provenance.artifact_id != change.evidence.span.artifact_id:
                continue
            artifact = next(a for a in artifacts if a.artifact_version_id == subject.provenance.artifact_version_id)
            transitions = sum(len(re.findall(r'供应商(?:由|从)[^，,。；\n]+?(?:改为|改用|改成)[^，,。；\n]+', s))
                              for _, _, s, asserted in _sentences(artifact.content) if asserted)
            if transitions != 1:
                continue
            positive = re.fullmatch(r'\s*因为该供应商更换(?:后)?[，,]?(?:此任务|本任务)必须(?P<action>调整|适配|补测|增加|等待)(?P<work>[^。]+)[。]?', sentence)
            own_keys = {key for key, _ in _keys(subject)}
            local_no = re.fullmatch(r'\s*(?P<key>\S+?)无需因本次供应商更换做任何调整(?:[，,][^。]+)?[。]?', sentence)
            negative = local_no if local_no and local_no['key'] in own_keys else None
            if not positive and not negative:
                continue
        if positive:
            # The stated adjustment must be work of this observation. A causal
            # statement about another task is not evidence for this endpoint.
            work = re.split('[，,]', positive['work'])[0].rstrip('。')
            if work not in subject.raw_text:
                continue
        quote = proof_quote(proposal, start, end, 'DIRECT_IMPACT')
        (yes if positive else no).append(quote)
    return yes, no
