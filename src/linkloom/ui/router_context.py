"""Compute request-local authority and time facts before model routing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re

from linkloom.indexing.bm25 import BM25Index, tokenize_for_index
from linkloom.indexing.models import IndexDocument
from linkloom.temporal_graph.dates import date_tokens
from linkloom.temporal_graph.models import digest
from linkloom.ui.scope_discovery import (
    RetrievalConfig, _mentions, _relation_phrases, resolve_query_date, retrieve_raw, workspace_snapshot,
)
from linkloom.ui.structured_router import CONFIG, SAFETY, RouterInvalid, normalize_mention

# Existing date parsing handles timestamps and invalid/relative dates. Mask
# complete timestamps before lexing meeting-date tokens, so they are not split.
_TIMESTAMP = re.compile(r"(?<![\d/.-])\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)?(?![\d/.-])", re.I)
_FIRST_PERSON_WORKSPACE = re.compile(r"\b(?:we|our|ours|us)\b|我们|咱们|我司", re.I)


@dataclass(frozen=True)
class RouteTime:
    role: str
    value: str | tuple[str, str]
    spans: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class RouteCandidate:
    domain: str
    kind: str
    label: str
    anchors: tuple[str, ...]
    current_count: int
    as_of_count: int | None
    history_count: int
    conflict: bool
    identity: tuple[str, ...]
    matched_anchors: tuple[str, ...] = ()
    default_references: tuple[str, ...] = ()

    def public(self):
        return dict(domain=self.domain, kind=self.kind, label=self.label, confirmed=True,
                    current_count=self.current_count, as_of_count=self.as_of_count,
                    history_count=self.history_count, conflict=self.conflict,
                    matched_terms=self.matched_anchors)


@dataclass(frozen=True)
class RouterContext:
    query: str
    workspace: str
    query_time: str
    time_options: dict[str, RouteTime]
    required_as_of: str | None
    date_spans: tuple[tuple[int, int], ...]
    unresolved_time: bool
    candidates: dict[str, RouteCandidate]
    total: int
    snapshot: str
    graph_available: bool
    default_subject: str | None = None

    def public(self):
        return dict(question=self.query, workspace_scope="CURRENT_ONLY", query_time=self.query_time,
                    available_graph=self.graph_available, required_as_of=self.required_as_of,
                    unresolved_time=self.unresolved_time, total=self.total, returned=len(self.candidates),
                    coverage="truncated" if self.total > len(self.candidates) else "complete",
                    candidates=[dict(id=k, **v.public()) for k, v in self.candidates.items()],
                    time_options=[dict(id=k, role=v.role, value=v.value, spans=sorted(v.spans))
                                  for k, v in self.time_options.items()],
                    default_subject=self.default_subject)


def time_context(query, selected):
    timestamps = [(m.group(), m.start(), m.end()) for m in _TIMESTAMP.finditer(query)]
    masked = _TIMESTAMP.sub(lambda m: " " * len(m.group()), query)
    tokens = sorted(timestamps + date_tokens(masked), key=lambda item: item[1])
    if len(tokens) > 4:
        raise RouterInvalid("ROUTER_INPUT_LIMIT")
    spans = tuple((start, end) for _, start, end in tokens)
    point, error = resolve_query_date(query, selected)
    options = {}
    if point is not None:
        options["point"] = RouteTime("AS_OF", point, frozenset(spans))
    for i, (value, start, end) in enumerate(tokens):
        if "T" not in value:
            options[f"meeting{i}"] = RouteTime("MEETING_DATE", value, frozenset(((start, end),)))
    if len(tokens) == 2:
        options["range"] = RouteTime("RANGE", (tokens[0][0], tokens[1][0]), frozenset(spans))
    return options, ("point" if point else "unresolved") if selected else None, spans, bool(error), point


def _decision_candidates(query, snapshot, view, point, embedder, default_subject, references):
    # Use the existing retrieval seam before the legacy entity/intent vetoes.
    rows = retrieve_raw(query, snapshot, RetrievalConfig(embedder))
    # Keep weak recall visible after stronger lexical and dense hits. Its
    # presence in the catalogue grants no authority to bind it.
    rows.sort(key=lambda row: not (row["topic_anchored"] or row["history_anchor"] or row["dense_score"] is not None))
    # A small reviewed catalogue can be sent in full when lexical recall
    # sees no strong topic. Never infer an alias from the added entries.
    if not any(row["topic_anchored"] or row["history_anchor"] for row in rows) and len(snapshot.slots) <= CONFIG["max_candidates"] // 2:
        present = {row["slot_id"] for row in rows}
        rows.extend(dict(slot, aliases=sorted(_relation_phrases(slot["relation"], snapshot.resolver)
                                              | set(slot.get("aliases", []))))
                    for slot in snapshot.slots if slot["slot_id"] not in present)
    for row in rows:
        subject, relation = row["subject"], row["relation"]
        current = view(subject, relation, None)
        dated = view(subject, relation, point) if point else None
        effective = dated or current
        anchors = tuple(dict.fromkeys([subject, relation, *row.get("aliases", []),
            *row.get("subject_aliases", []), *row.get("values", [])]))
        yield RouteCandidate("DECISION", "decision_slot", f"{subject} · {relation}"[:160], anchors,
            int(current["status"] == "FOUND"), int(dated["status"] == "FOUND") if dated else None,
            len(effective["history"]), "CONFLICT" in effective["status"], (subject, relation),
            tuple(sorted(a for a in anchors if _mentions(query.casefold(), a))[:8]),
            references if subject == default_subject else ())


def _graph_candidates(query, snapshot, now, point):
    if snapshot is None:
        return []
    records = {r["node_id"]: r for r in snapshot.records}
    incident = {}
    for edge in snapshot.edges:
        for key in {edge.source.node_id, edge.target.node_id}:
            incident.setdefault(key, []).append(edge)
    docs = []
    nodes = {node.node_id: node for node in snapshot.nodes}
    for node in snapshot.nodes:
        record = records.get(node.node_id, {})
        if node.kind == "task_observation" or not node.label:
            continue
        text = record.get("title") or node.label
        docs.append(IndexDocument(snapshot.workspace_id, node.node_id, node.node_id,
                                  "/graph/" + node.node_id, text, node.node_id))
    if not docs:
        return []
    index = BM25Index()
    index.build(docs)
    query_terms = set(tokenize_for_index(query))
    # Local lexical recall for the router catalogue. The graph worker still
    # performs the existing host binding and evidence/temporal validation.
    hits = {h.resource_id: h.score for h in index.search(query, workspace_id=snapshot.workspace_id, top_k=len(docs))}
    relevant = [d for d in docs if d.content.casefold() in query.casefold()
                or query_terms.intersection(tokenize_for_index(d.content))]
    relevant.sort(key=lambda d: (d.content.casefold() not in query.casefold(), -hits.get(d.resource_id, 0), d.resource_id))
    candidates = []
    for doc in relevant:
        node = nodes[doc.resource_id]
        record = records.get(node.node_id, {})
        states = record.get("states", [])
        edges = incident.get(node.node_id, ())
        def count(when):
            from linkloom.temporal_graph.meeting_query import _task_states, _valid
            if states:
                return len(_task_states(record, when))
            if record.get("valid_from"):
                return int(_valid(record, when))
            return sum(edge.time_match(when) == "VALID" and not edge.blocker for edge in edges)
        domain = "TASK" if node.kind == "task" else "RELATION"
        candidates.append(RouteCandidate(domain, node.kind, doc.content[:160], (doc.content,), count(now),
            count(point) if point else None, len(states) or len(edges),
            any(e.blocker and e.time_match(point or now) == "VALID" for e in edges) or
            bool(states and count(point or now) > 1), (node.node_id,)))
    return candidates


def build_context(query, workspace, slots, resolver, *, view, graph_snapshot=None,
                  selected_as_of=None, query_time=None, source_snapshot="", embedder=None,
                  graph_available=False):
    if graph_snapshot is not None and (graph_snapshot.workspace_id != workspace or
            any(node.workspace_id != workspace for node in graph_snapshot.nodes)):
        raise PermissionError("WORKSPACE_DENIED")
    now = query_time or datetime.now(UTC).isoformat()
    options, required, spans, unresolved, point = time_context(query, selected_as_of)
    reviewed = workspace_snapshot(slots, resolver, workspace)
    subjects = {slot["subject"] for slot in reviewed.slots}
    references = tuple(dict.fromkeys(_FIRST_PERSON_WORKSPACE.findall(query)))
    default_subject = next(iter(subjects)) if references and len(subjects) == 1 else None
    decision = list(_decision_candidates(query, reviewed, view, point, embedder, default_subject, references))
    graph = _graph_candidates(query, graph_snapshot, now, point)
    ranked = decision + graph
    limit = CONFIG["max_candidates"]
    # Reserve space for graph/task candidates; otherwise decision hits could
    # exhaust the catalogue before the model sees the named task.
    primary = decision[:limit//2] + graph[:limit//2]
    selected = (primary + decision[limit//2:] + graph[limit//2:])[:limit]
    catalog = {f"c{i}": candidate for i, candidate in enumerate(selected)}
    signature = digest(dict(source_snapshot=source_snapshot, workspace=workspace,
        candidates=[(c.identity, c.public()) for c in ranked], now=now, reviewed_slots=slots,
        graph_records=list(graph_snapshot.records) if graph_snapshot else [],
        graph_edges=[e.to_dict() for e in graph_snapshot.edges] if graph_snapshot else []))
    return RouterContext(query, workspace, now, options, required, spans, unresolved,
                         catalog, len(ranked), signature, graph_available, default_subject)


def bind_candidate(context, route):
    """Only a unique grounded candidate may authorize an executor selection."""
    if not route.candidate_ids:
        raise RouterInvalid("ENTITY_NOT_FOUND")
    if len(route.candidate_ids) != 1 or context.total > len(context.candidates):
        raise RouterInvalid("ENTITY_AMBIGUOUS")
    candidate = context.candidates[route.candidate_ids[0]]
    mentions = {normalize_mention(text) for text, start, end in route.mentions
                if (start, end) not in context.date_spans}
    def grounds(row):
        anchors = {normalize_mention(value) for value in row.anchors if value}
        references = {normalize_mention(value) for value in row.default_references}
        # A workspace pronoun can supplement an actual topic anchor only.
        return bool(mentions & anchors) and mentions <= anchors | references
    if not grounds(candidate):
        raise RouterInvalid("ENTITY_NOT_FOUND")
    if candidate.conflict or sum(grounds(c) for c in context.candidates.values() if c.domain == route.domain) != 1:
        raise RouterInvalid("ENTITY_AMBIGUOUS")
    if not candidate.history_count and candidate.kind != "meeting":
        raise RouterInvalid("NO_CONFIRMED_RECORD")
    return candidate


@dataclass(frozen=True)
class RouterAdmission:
    executor: str | None
    reason: str
    candidate: RouteCandidate | None = None
    as_of: str | None = None


def admit_route(context, route):
    """Choose only installed read paths after authority and semantic validation."""
    if route.reason_code in SAFETY:
        return RouterAdmission(None, route.reason_code)
    if route.decision != 'ROUTE':
        candidate = None
        if route.candidate_ids:
            try:
                candidate = bind_candidate(context, route)
            except RouterInvalid:
                pass
        return RouterAdmission(None, route.reason_code, candidate)
    if route.time_role in {'RANGE', 'UNRESOLVED'}:
        return RouterAdmission(None, 'TIME_SCOPE_UNRESOLVED')
    graph_intents = {'TASK': {'CURRENT', 'AS_OF', 'LIST', 'HISTORY'},
                     'RELATION': {'RELATION_MULTIHOP', 'LIST', 'HISTORY', 'COMPARE'}}
    decision = route.domain == 'DECISION' and route.intent in {'CURRENT', 'AS_OF', 'INVESTIGATION'}
    graph = route.intent in graph_intents.get(route.domain, ()) and context.graph_available
    if not decision and not graph or decision and route.time_role == 'MEETING_DATE':
        return RouterAdmission(None, 'UNSUPPORTED_QUERY')
    try:
        candidate = bind_candidate(context, route)
    except RouterInvalid as exc:
        return RouterAdmission(None, str(exc))
    as_of = context.time_options[route.time_ref_ids[0]].value if route.time_role == 'AS_OF' else None
    count = candidate.as_of_count if as_of else candidate.current_count
    if route.intent in {'CURRENT', 'AS_OF'} and not count:
        return RouterAdmission(None, 'NO_VALID_RECORD_AT_TIME', candidate, as_of)
    return RouterAdmission('graph' if graph else 'decision', 'NONE', candidate, as_of)
