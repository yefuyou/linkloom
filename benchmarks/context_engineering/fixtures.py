"""Runtime-visible synthetic inputs. No evaluation Gold is imported here."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from linkloom.context.models import ContextResource, ResourceType
from linkloom.context.policy import EvidenceGroup
from linkloom.context.recovery import EvidenceRef, SourceSnapshot, digest
from linkloom.retrieval_v2.models import RetrievedEvidence


@dataclass(frozen=True)
class Passage:
    label: str
    ref: EvidenceRef
    quote: str
    topic: str
    link_group: str
    polarity: str = 'support'

    def retrieved(self, rank: int, source: SourceSnapshot | None) -> RetrievedEvidence:
        return RetrievedEvidence(self.ref.evidence_id, self.ref.source_ref, self.ref.logical_path,
            1 / rank, rank, 'synthetic', self.ref.resource_id,
            {'workspace_id': 'synthetic', 'text': self.quote, 'current': bool(source and source.current),
             'active': bool(source and source.resource.active), 'authorized': bool(source and source.authorized)})


@dataclass(frozen=True)
class Scenario:
    case_id: str
    subgoals: tuple[str, ...]
    candidates: tuple[Passage, ...]
    archived: tuple[Passage, ...]
    sources: dict[str, SourceSnapshot]
    operations: tuple[str, ...] = ()


def document(name: str, rows: list[tuple[str, str, str, str, str]]) -> tuple[list[Passage], SourceSnapshot]:
    content = '\n'.join(row[1] for row in rows)
    timestamp = datetime(2026, 10, 19, tzinfo=UTC)
    resource = ContextResource('synthetic', name, name, f'/{name}.md', '/', 1,
        timestamp, digest(content), f'fixture:{name}', ResourceType.DOCUMENT, timestamp, 1)
    source = SourceSnapshot(resource, content)
    passages, offset = [], 0
    for label, text, topic, group, polarity in rows:
        passages.append(Passage(label, EvidenceRef.from_snapshot(source, offset, offset + len(text)),
                                text, topic, group, polarity))
        offset += len(text) + 1
    return passages, source


def noise(count: int, *, long_first=False):
    return document('noise', [(f'noise-{i}', ('无关日志。' * 1800 if i == 0 and long_first else
        f'无关项目 Z{i} 的活动记录，与当前项目无关。'), 'unrelated', f'noise-{i}', 'support') for i in range(count)])


def declared_groups(passages: tuple[Passage, ...], goals: tuple[str, ...]) -> tuple[EvidenceGroup, ...]:
    """Group by observable fixture link/topic tags, analogous to task-plan hints.

    Includes all matching groups, including redundant/stale passages. No Gold,
    required label, answer or correctness flag enters this function.
    """
    groups: dict[str, list[Passage]] = {}
    for p in passages:
        if p.topic in goals:
            groups.setdefault(p.link_group, []).append(p)
    return tuple(EvidenceGroup(name, tuple(p.ref.evidence_id for p in members),
        tuple(dict.fromkeys(p.topic for p in members)), any(p.polarity == 'counter' for p in members))
        for name, members in groups.items())


def scenarios() -> tuple[Scenario, ...]:
    cases = []
    for name, hops in (('two_hop', 2), ('three_hop', 3), ('scattered_same_file', 3)):
        rows = [('owner', '项目松林的负责人是林然。', 'owner', 'chain', 'support'),
                ('leader', '林然的直属领导是陈溪。', 'leader', 'chain', 'support'),
                ('budget', '陈溪批准的预算为 80 万元。', 'budget', 'chain', 'support')][:hops]
        evidence, source = document(name, rows)
        junk, junk_source = noise(18, long_first=True)
        # Same-file case has three separated passages with intervening noise.
        if name == 'scattered_same_file':
            evidence, source = document(name, [rows[0], ('gap1', '会议室使用记录。', 'unrelated', 'gap1', 'support'),
                rows[1], ('gap2', '茶水采购记录。', 'unrelated', 'gap2', 'support'), rows[2]])
        candidates = (*junk[:6], *evidence[:1], *junk[6:10], *evidence[1:], *junk[10:])
        cases.append(Scenario(name, tuple(r[2] for r in rows), candidates, (),
                              {source.resource.resource_id: source, 'noise': junk_source}))
    support, s1 = document('proposal', [('support', '采购建议选择供应商甲。', 'supplier', 'supplier-dispute', 'support')])
    counter, s2 = document('objection', [('counter', '安全审查否决供应商甲，必须继续调查。', 'supplier', 'supplier-dispute', 'counter')])
    junk, js = noise(20)
    cases.append(Scenario('cross_document_conflict', ('supplier',), (*support, *junk[:12], *counter, *junk[12:]), (),
                          {'proposal': s1, 'objection': s2, 'noise': js}))
    old, so = document('old', [('old-supplier', '旧决定采用供应商甲。', 'supplier', 'old-decision', 'support'),
                               ('old-plan', '旧计划要求六月上线。', 'plan', 'old-decision', 'support')])
    current, sn = document('revision', [('new-supplier', '复核后供应商改为乙，甲的决定已经撤销。', 'supplier', 'revision', 'support'),
        ('new-plan', '新计划改为七月上线，六月版本已作废。', 'plan', 'revision', 'counter')])
    cases.append(Scenario('supplier_and_plan_revision', ('supplier', 'plan'), (*old, *junk[:10], *current), (),
                          {'old': replace(so, current=False), 'revision': sn, 'noise': js}))
    key, sk = document('needle', [('needle', '项目松林的预算负责人是周晴。', 'owner', 'needle', 'support')])
    many, sm = noise(180)
    cases.append(Scenario('large_irrelevant_pool', ('owner',), (*many[:120], *key, *many[120:]), (),
                          {'needle': sk, 'noise': sm}))
    archived, sa = document('archive', [('archived-owner', '项目松林负责人是林然。', 'owner', 'history-chain', 'support'),
        ('archived-leader', '林然的直属领导是陈溪。', 'leader', 'history-chain', 'support')])
    operations = tuple(f'工具操作 {i}: 返回大量无关结果。' + '日志明细。' * 80 for i in range(100))
    cases.append(Scenario('long_history_short_context', ('owner', 'leader'), tuple(junk), tuple(archived),
                          {'archive': sa, 'noise': js}, operations))
    expired, se = document('expired', [('expired', '供应商甲目前仍然有效。', 'supplier', 'expired', 'support')])
    changed = replace(se, resource=replace(se.resource, version=2, content_hash=digest('供应商乙是当前决定。')),
                      content='供应商乙是当前决定。')
    deleted, sd = document('deleted', [('deleted', '六月计划有效。', 'plan', 'deleted', 'support')])
    # Updated/deleted originals remain in immutable archive, but cannot be revived.
    cases.append(Scenario('source_update_and_invalidation', ('supplier', 'plan'), (), (*expired, *deleted),
                          {'expired': changed}, operations[:20]))
    return tuple(cases)
