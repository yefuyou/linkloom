"""Deterministic, bounded decomposition for a few TeamDecision relation chains."""

from __future__ import annotations

import re
from datetime import date

from linkloom.runtime.task_plan import TaskPlan


_LATIN_ENTITY_TOKEN = r"[A-Z][A-Za-z0-9]*(?:[.'’&/-][A-Za-z0-9]+)*"
_CJK_ENTITY_TOKEN = r"[\u3400-\u4dbf\u4e00-\u9fff]+(?:[A-Za-z0-9][A-Za-z0-9.'’&/-]*)?"
_ENTITY_TOKEN = rf"(?:{_LATIN_ENTITY_TOKEN}|{_CJK_ENTITY_TOKEN})"
_ENTITY = rf"{_ENTITY_TOKEN}(?:\s+{_ENTITY_TOKEN}){{0,5}}"
_TAIL = r"\s*[?.!]*\s*$"
_ZH_TAIL = r"\s*[?？!！。]*\s*$"

_JOIN = r"(?:,\s*(?:and\s+)?|\s+and\s+)"

_REPLACEMENT_OWNER_LEADER_DATE = re.compile(
    rf"^(?i:which plan replaced)\s+(?P<entity>{_ENTITY})"
    rf"{_JOIN}(?i:who owns (?:it|the replacement|the new plan))"
    rf"{_JOIN}(?i:who is that owner(?:'s|’s) direct leader)"
    rf"{_JOIN}(?i:when did the replacement take effect)"
    + _TAIL
)
_REPLACEMENT_OWNER_LEADER = re.compile(
    rf"^(?i:which plan replaced)\s+(?P<entity>{_ENTITY})"
    rf"{_JOIN}(?i:who owns (?:it|the replacement|the new plan))"
    rf"{_JOIN}(?i:who is that owner(?:'s|’s) direct leader)"
    + _TAIL
)
_REPLACEMENT_OWNER = re.compile(
    rf"^(?i:which plan replaced)\s+(?P<entity>{_ENTITY})\s+"
    rf"(?i:and who owns (?:it|the replacement|the new plan|that new plan))" + _TAIL
)
_OWNER_LEADER = re.compile(
    rf"^(?i:who owns)\s+(?P<entity>{_ENTITY})\s+"
    rf"(?i:and (?:who is (?:their|that owner's|that owner’s) "
    rf"(?:(?:direct|line) )?(?:leader|supervisor|manager)|"
    rf"who (?:supervises|manages) (?:the|that) owner))" + _TAIL
)
_LEADER_OF_OWNER = re.compile(
    rf"^(?i:who is the (?:direct )?(?:leader|supervisor|manager) of the owner of)"
    rf"\s+(?P<entity>{_ENTITY})"
    + _TAIL
)

_ZH_OWNER_LEADER = re.compile(
    rf"^谁负责\s*(?P<entity>{_ENTITY})\s*[?？]\s*"
    rf"(?:这位|该|其)?负责人(?:的)?(?:直属领导|直接领导|上级)(?:是谁|是?谁)"
    + _ZH_TAIL
)
_ZH_PASSIVE_REPLACEMENT_OWNER_LEADER = re.compile(
    rf"^(?P<entity>{_ENTITY})\s*被(?:哪个|什么)(?:方案|计划)?"
    rf"(?:替代|取代|接替)了?\s*[?？]\s*"
    rf"谁是(?:它|该方案|该计划|这个方案)?的?(?:负责人|所有者)[?？]\s*"
    rf"(?:该|这位|其)?负责人(?:的)?(?:直属领导|直接领导|上级)(?:是谁|是?谁)"
    + _ZH_TAIL
)
_ZH_ACTIVE_REPLACEMENT_OWNER_LEADER_DATE = re.compile(
    rf"^(?:哪个|什么)(?:方案|计划)\s*(?:替代|取代|接替)了?\s*"
    rf"(?P<entity>{_ENTITY})[，,]\s*"
    rf"(?:谁负责(?:该方案|该计划|这个方案|它)|"
    rf"谁是(?:该方案|该计划|这个方案|它)的?(?:负责人|所有者))"
    rf"[，,]\s*(?:(?:该|这位|其)?(?:负责人|所有者)的)?"
    rf"(?:直属领导|直接领导|上级)(?:是谁|是?谁)"
    rf"[，,]\s*(?:替代|取代|接替)何时生效"
    + _ZH_TAIL
)
_ZH_ACTIVE_REPLACEMENT_OWNER_LEADER = re.compile(
    rf"^(?:哪个|什么)(?:方案|计划)\s*(?:替代|取代|接替)了?\s*"
    rf"(?P<entity>{_ENTITY})(?:[，,]\s*|[?？]\s*)"
    rf"(?:谁负责(?:该方案|该计划|这个方案|它)|"
    rf"谁是(?:该方案|该计划|这个方案|它)的?(?:负责人|所有者))"
    rf"(?:[，,]\s*|[?？]\s*)(?:(?:该|这位|其)?(?:负责人|所有者)的)?"
    rf"(?:直属领导|直接领导|上级)(?:是谁|是?谁)"
    + _ZH_TAIL
)


def plan_team_decision_query(query: object) -> TaskPlan | None:
    """Build an answer-free TaskPlan for recognized two/three-hop query forms.

    The entity value is copied from the query. Facts and downstream entities
    are still obtained by the normal verified search/read Tool Ledger path.
    Unknown query shapes return ``None`` and are not presented as planned.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 2048:
        return None
    normalized = query.strip()
    match = _ZH_ACTIVE_REPLACEMENT_OWNER_LEADER_DATE.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("successor", f"哪个方案替代了 {entity}？", ()),
                ("owner", "谁负责{verified_entity}？", ("successor",)),
                ("leader", "{verified_entity}的直属领导是谁？", ("owner",)),
                ("effective_date", f"{entity} 的替代何时生效？", ()),
            ],
        )
    match = (
        _ZH_PASSIVE_REPLACEMENT_OWNER_LEADER.fullmatch(normalized)
        or _ZH_ACTIVE_REPLACEMENT_OWNER_LEADER.fullmatch(normalized)
    )
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("successor", f"哪个方案替代了 {entity}？", ()),
                ("owner", "谁负责{verified_entity}？", ("successor",)),
                ("leader", "{verified_entity}的直属领导是谁？", ("owner",)),
            ],
        )
    match = _ZH_OWNER_LEADER.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("owner", f"谁负责 {entity}？", ()),
                ("leader", "{verified_entity}的直属领导是谁？", ("owner",)),
            ],
        )
    match = _REPLACEMENT_OWNER_LEADER_DATE.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("successor", f"What plan replaced {entity}?", ()),
                ("owner", "Who owns {verified_entity}?", ("successor",)),
                ("leader", "Who is the direct leader of {verified_entity}?", ("owner",)),
                ("effective_date", f"When did the replacement for {entity} take effect?", ()),
            ],
        )
    match = _REPLACEMENT_OWNER_LEADER.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("successor", f"What plan replaced {entity}?", ()),
                ("owner", "Who owns {verified_entity}?", ("successor",)),
                ("leader", "Who is the direct leader of {verified_entity}?", ("owner",)),
            ],
        )
    match = _REPLACEMENT_OWNER.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("successor", f"What plan replaced {entity}?", ()),
                ("owner", "Who owns {verified_entity}?", ("successor",)),
            ],
        )
    match = _OWNER_LEADER.fullmatch(normalized) or _LEADER_OF_OWNER.fullmatch(normalized)
    if match:
        entity = match.group("entity").strip()
        return _build_plan(
            normalized,
            [
                ("owner", f"Who owns {entity}?", ()),
                ("leader", "Who is the direct leader of {verified_entity}?", ("owner",)),
            ],
        )
    return None


def _build_plan(
    goal: str,
    rows: list[tuple[str, str, tuple[str, ...]]],
) -> TaskPlan:
    return TaskPlan.from_dict({
        "goal": goal,
        "subgoals": [
            {"subgoal_id": item_id, "question": question, "depends_on": list(dependencies)}
            for item_id, question, dependencies in rows
        ],
    })


def plan_relation_investigation(query: object) -> TaskPlan | None:
    """Opt-in source-relation plan, separate from approved temporal decisions.

    This compiler recognizes a bounded grammar, not arbitrary natural language.
    It copies only the user's root entity; dependent entities stay placeholders.
    Existing product routing and the historical template planner are unchanged.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 2048:
        return None
    goal = query.strip()
    text, snapshot = goal, None
    dated = re.match(r"^(?:As of |On |截至\s*)(\d{4}-\d{2}-\d{2})[,，]?\s+", text, re.I)
    if dated:
        try:
            snapshot = date.fromisoformat(dated[1]).isoformat()
        except ValueError:
            return None
        text = text[dated.end():]
    alias_patterns = (
        rf"^(?i:who owns the project behind)\s+(?P<entity>{_ENTITY})\s+(?i:and who (?:leads|manages|supervises) that owner){_TAIL}",
        rf"^(?i:which project does)\s+(?P<entity>{_ENTITY})\s+(?i:alias, who owns it, and who is that owner's direct leader){_TAIL}",
    )
    match = next((m for p in alias_patterns if (m := re.fullmatch(p, text))), None)
    alias = match is not None
    if match is None:
        match = _OWNER_LEADER.fullmatch(text) or _LEADER_OF_OWNER.fullmatch(text)
    if match is None:
        match = re.fullmatch(
            rf"(?P<entity>{_ENTITY})\s*的负责人(?:向谁直接汇报|的直属领导是谁){_ZH_TAIL}", text,
        )
    if match is None:
        return None
    entity = match['entity'].strip()
    dated_prefix = f"As of {snapshot} " if snapshot else ""
    rows = []
    if alias:
        rows.append(('alias', f'Which project does {entity} alias?', (), 'alias_of_project'))
    rows.append(('owner', dated_prefix + f"Who owns {'{verified_entity}' if alias else entity}?",
                 ('alias',) if alias else (), 'owner_of_project'))
    rows.append(('leader', dated_prefix + 'Who is the direct leader of {verified_entity}?',
                 ('owner',), 'reports_to'))
    return TaskPlan.from_dict({
        'schema_version': 'task-plan/v2', 'goal': goal,
        'subgoals': [{'subgoal_id': key, 'question': question, 'depends_on': list(parents),
                      'relation_path': [relation]} for key, question, parents, relation in rows],
    })


__all__ = ["plan_team_decision_query", "plan_relation_investigation"]
