"""Bounded greedy, all-or-none evidence selection using caller task hints."""

from __future__ import annotations

from typing import Callable, Sequence

from .policy import EvidenceGroup, EvidenceSelectionResult, SubgoalCoverage


def select_evidence(
    candidate_ids: Sequence[str], *, groups: Sequence[EvidenceGroup],
    subgoals: Sequence[str], max_evidence: int,
    fits: Callable[[tuple[str, ...]], bool], inspected: int, omitted: int,
) -> tuple[EvidenceSelectionResult, dict[str, str]]:
    known = set(candidate_ids)
    grouped: set[str] = set()
    group_names: set[str] = set()
    for group in groups:
        if group.group_id in group_names or grouped.intersection(group.evidence_ids):
            raise ValueError('evidence groups must have unique IDs and disjoint members')
        group_names.add(group.group_id)
        grouped.update(group.evidence_ids)
    # Internal singleton IDs cannot collide with caller group IDs.
    units = [(g.group_id, g.evidence_ids, g.subgoal_ids, g.conflict) for g in groups]
    units.extend((None, (i,), (), False) for i in candidate_ids if i not in grouped)
    ranks = {i: rank for rank, i in enumerate(candidate_ids)}
    selected: tuple[str, ...] = ()
    selected_groups: list[str] = []
    covered: set[str] = set()
    rejected: dict[str, str] = {}
    while units:
        unit = min(units, key=lambda u: (
            -len(set(u[2]) - covered), -int(u[3]),
            min((ranks.get(i, len(ranks)) for i in u[1]), default=len(ranks)),
            u[0] or u[1][0],
        ))
        units.remove(unit)
        name, members, goals, _conflict = unit
        reason = None
        if not set(members).issubset(known):
            reason = 'GROUP_INCOMPLETE'
        elif len(selected) + len(members) > max_evidence:
            reason = 'LOW_RANK'
        elif not fits((*selected, *members)):
            reason = 'BUDGET'
        if reason:
            rejected.update((i, reason) for i in members if i in known)
        else:
            selected += members
            covered.update(goals)
            if name is not None:
                selected_groups.append(name)
    coverage = []
    for goal in dict.fromkeys((*subgoals, *(s for g in groups for s in g.subgoal_ids))):
        required = {i for g in groups if goal in g.subgoal_ids for i in g.evidence_ids}
        missing = tuple(sorted(required - set(selected)))
        coverage.append(SubgoalCoverage(goal, bool(required) and not missing, missing,
            'NO_CANDIDATES' if not required else 'INCOMPLETE_SELECTION' if missing else None))
    return EvidenceSelectionResult(selected, tuple(selected_groups), tuple(coverage), inspected, omitted), rejected
