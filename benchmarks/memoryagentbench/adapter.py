"""Gold-isolated input adapter for MemoryAgentBench FactConsolidation."""

from __future__ import annotations

import re
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path


TARGET_SOURCE = "factconsolidation_sh_6k"
GOLD_FREE_INPUT_COLUMNS = ("context", "questions", "metadata.qa_pair_ids", "metadata.source")
_ORDERED_FACT = re.compile(r"^\s*(?P<ordinal>\d+)\.\s+(?P<statement>\S.+?)\s*$")
_FRONTED_COPULA = re.compile(
    r"^The\s+(?P<left>.+?)\s+is\s+(?P<value>.+?)[.!]?$",
    re.IGNORECASE,
)
_WHERE_RELATION = re.compile(
    r"^The\s+(?P<relation>.+?)\s+where\s+(?P<subject>.+?)\s+"
    r"(?P<tense>was|is)\s+educated\s+is\s+(?P<value>.+?)[.!]?$",
    re.IGNORECASE,
)
_COPULA_PREDICATE = re.compile(
    r"^(?P<subject>.+?)\s+(?:was|were|is|are)\s+(?P<tail>.+)$",
    re.IGNORECASE,
)
_RELATIVE_AGENT = re.compile(
    r"^(?:The|A|An)\s+(?P<agent_role>.+?)\s+(?:that|which|who)\s+"
    r"(?P<verb>[A-Za-z]+(?:ed|s))\s+(?P<object>.+?)\s+is\s+(?P<agent>.+?)[.!]?$",
    re.IGNORECASE,
)
_PREDICATE_PREPOSITION = re.compile(
    r"\b(?P<preposition>by|in|at|of|to|with|for|from|into|onto|on|as)\b",
    re.IGNORECASE,
)
_ACTIVE_VERB_FORM = re.compile(r"\b[A-Za-z]+(?:ed|ing|s)\b", re.IGNORECASE)
_ACTIVE_COMPLEMENT_START = re.compile(
    r"^\s+(?:a|an|the)\b|^\s*(?:by|in|at|of|to|with|for|from|into|onto|on|as)\b",
    re.IGNORECASE,
)
_POSSESSIVE_COPULA_RAW_RELATION = re.compile(
    r"^(?P<subject>.+?)(?:'s|’s)\s+(?P<predicate>[^.!?]+?)\s+"
    r"(?:was|were|is|are)\s+(?P<value>.+?)[.!?]*$",
    re.IGNORECASE,
)
_ARTICLES = {"a", "an", "the"}


@dataclass(frozen=True, slots=True)
class BenchmarkQuestion:
    question_id: str
    question: str


@dataclass(frozen=True, slots=True)
class OrderedFact:
    ordinal: int
    fact_id: str
    statement: str
    subject_key: str | None
    value: str | None
    extraction_rule: str | None
    subject: str | None = None
    predicate: str | None = None
    raw_predicate: str | None = None


@dataclass(frozen=True, slots=True)
class _FactProposition:
    subject: str
    predicate: str
    value: str
    subject_key: str
    extraction_rule: str


@dataclass(frozen=True, slots=True)
class FactConsolidationCase:
    source: str
    case_id: str
    workspace_id: str
    context: str
    facts: tuple[OrderedFact, ...]
    questions: tuple[BenchmarkQuestion, ...]
    input_projection_columns: tuple[str, ...] = ()


def load_factconsolidation_case(
    parquet_path: str | Path,
    *,
    source: str = TARGET_SOURCE,
    question_limit: int = 10,
    question_offset: int = 0,
) -> FactConsolidationCase:
    """Load context and query inputs without reading any answer/Gold columns."""
    if isinstance(question_limit, bool) or not isinstance(question_limit, int) or question_limit <= 0:
        raise ValueError("question_limit must be positive")
    if isinstance(question_offset, bool) or not isinstance(question_offset, int) or question_offset < 0:
        raise ValueError("question_offset must be a non-negative integer")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:  # pragma: no cover - environment-specific message
        raise RuntimeError(
            "MemoryAgentBench's benchmark-only Parquet adapter requires pyarrow."
        ) from error

    input_projection_columns = list(GOLD_FREE_INPUT_COLUMNS)
    table = parquet.read_table(str(parquet_path), columns=input_projection_columns)
    rows = table.to_pylist()
    matches = [row for row in rows if row.get("source") == source]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {source!r} row; found {len(matches)}"
        )
    row = matches[0]
    context = row.get("context")
    questions = row.get("questions")
    question_ids = row.get("qa_pair_ids")
    if not isinstance(context, str) or not isinstance(questions, list):
        raise ValueError("FactConsolidation row has invalid context/questions fields")
    if not isinstance(question_ids, list) or len(question_ids) != len(questions):
        raise ValueError("FactConsolidation question IDs do not align with questions")

    selected_start = min(question_offset, len(questions))
    selected_stop = min(question_offset + question_limit, len(questions))
    normalized_questions: list[BenchmarkQuestion] = []
    for question_id, question in zip(
        question_ids[selected_start:selected_stop],
        questions[selected_start:selected_stop],
        strict=True,
    ):
        if not isinstance(question_id, str) or not isinstance(question, str):
            raise ValueError("FactConsolidation question and ID values must be strings")
        normalized_questions.append(
            BenchmarkQuestion(question_id=question_id, question=question)
        )

    return FactConsolidationCase(
        source=source,
        case_id=f"{source}:{sha256(context.encode('utf-8')).hexdigest()[:16]}",
        workspace_id=(
            f"memoryagentbench:{source}:{sha256(context.encode('utf-8')).hexdigest()[:16]}"
        ),
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=tuple(normalized_questions),
        input_projection_columns=tuple(input_projection_columns),
    )


def parse_ordered_facts(context: str) -> list[OrderedFact]:
    """Parse the benchmark's serial-numbered facts, preserving their exact order."""
    if not isinstance(context, str):
        raise TypeError("context must be a string")
    facts: list[OrderedFact] = []
    for line in context.splitlines():
        if not re.match(r"^\s*\d+\.", line):
            continue
        match = _ORDERED_FACT.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed numbered fact line: {line[:80]!r}")
        ordinal = int(match.group("ordinal"))
        statement = match.group("statement").strip()
        proposition = _extract_fact_proposition(statement)
        facts.append(
            OrderedFact(
                ordinal=ordinal,
                fact_id=f"fact:{ordinal}",
                statement=statement,
                subject_key=proposition.subject_key if proposition else None,
                value=proposition.value if proposition else None,
                extraction_rule=proposition.extraction_rule if proposition else None,
                subject=proposition.subject if proposition else None,
                predicate=proposition.predicate if proposition else None,
                raw_predicate=(
                    proposition.predicate
                    if proposition
                    else _extract_unmapped_raw_predicate(statement)
                ),
            )
        )
    if not facts:
        raise ValueError("context contains no numbered facts")
    ordinals = [fact.ordinal for fact in facts]
    if ordinals != list(range(len(facts))):
        raise ValueError("fact ordinals must be unique, ordered, and contiguous from zero")
    return facts


def extract_fact_proposition(statement: str) -> tuple[str | None, str | None, str | None]:
    """Return the legacy memory key/value view of a parsed proposition."""
    proposition = _extract_fact_proposition(statement)
    if proposition is None:
        return None, None, None
    return proposition.subject_key, proposition.value, proposition.extraction_rule


def _extract_fact_proposition(statement: str) -> _FactProposition | None:
    """Extract a deterministic subject/predicate/object without case rules.

    Existing benchmark forms keep their established memory keys. Generic
    grammatical boundaries cover relation-first, passive, active, and
    relative-agent forms without a per-predicate allowlist.
    """
    text = statement.strip()
    where_relation = _WHERE_RELATION.fullmatch(text)
    if where_relation is not None:
        relation = _normalize_key(where_relation.group("relation"))
        subject = _clean_surface(where_relation.group("subject"))
        tense = where_relation.group("tense").casefold()
        value = _clean_surface(where_relation.group("value"))
        if relation and subject and value:
            return _FactProposition(
                subject=subject,
                predicate="educated at",
                value=value,
                subject_key=f"{relation} where {_normalize_key(subject)} {tense} educated",
                extraction_rule="relation_where_subject_educated",
            )
    relative_agent = _RELATIVE_AGENT.fullmatch(text)
    if relative_agent is not None:
        subject = _clean_surface(relative_agent.group("object"))
        value = _clean_surface(relative_agent.group("agent"))
        verb = _clean_surface(relative_agent.group("verb"))
        if subject and value and verb:
            predicate = f"{verb} by"
            return _FactProposition(
                subject=subject,
                predicate=predicate,
                value=value,
                subject_key=f"{_normalize_key(subject)} {_normalize_key(predicate)}",
                extraction_rule="relative_agent_clause",
            )

    front_loaded = _FRONTED_COPULA.fullmatch(text)
    if front_loaded is not None:
        left = front_loaded.group("left").strip()
        value = _clean_surface(front_loaded.group("value"))
        boundary = _choose_object_boundary(left)
        if boundary is not None:
            marker = boundary[0]
            relation = _normalize_key(f"{left[:marker.start()]} {marker.group()}")
            subject = _clean_surface(left[marker.end() :])
        else:
            relation = ""
            subject = ""
        if relation and subject and value:
            return _FactProposition(
                subject=subject,
                predicate=relation,
                value=value,
                subject_key=f"{relation} {_normalize_key(subject)}",
                extraction_rule="fronted_relation_subject_value",
            )

    copula = _COPULA_PREDICATE.fullmatch(text)
    if copula is not None:
        subject = _clean_surface(copula.group("subject"))
        tail = copula.group("tail").strip()
        boundary = _choose_object_boundary(tail)
        if subject and boundary is not None:
            marker, value = boundary
            predicate = _normalize_key(f"{tail[:marker.start()]} {marker.group()}")
            if predicate and value:
                return _FactProposition(
                    subject=subject,
                    predicate=predicate,
                    value=value,
                    subject_key=f"{_normalize_key(subject)} {predicate}",
                    extraction_rule="generic_copula_prepositional_object",
                )

    active = _extract_active_prepositional_clause(text)
    if active is not None:
        return active
    return None


def _choose_object_boundary(
    text: str,
) -> tuple[re.Match[str], str] | None:
    """Choose a grammatical preposition boundary while preserving object names.

    Prefer an explicit agent marker ("by"). Otherwise choose the earliest
    preposition followed by an entity-like phrase, which retains internal
    prepositions in names such as "United States of America". When the value
    itself is lowercase, use the final preposition as a conservative fallback.
    """
    candidates = [
        match for match in _PREDICATE_PREPOSITION.finditer(text)
        if _clean_surface(text[match.end() :])
    ]
    if not candidates:
        return None
    by_candidates = [match for match in candidates if match.group("preposition").casefold() == "by"]
    if by_candidates:
        selected = by_candidates[0]
    else:
        selected = next(
            (
                match for match in candidates
                if _looks_like_entity_phrase(_clean_surface(text[match.end() :]))
            ),
            candidates[-1],
        )
    value = _clean_surface(text[selected.end() :])
    return (selected, value) if value else None


def _extract_active_prepositional_clause(text: str) -> _FactProposition | None:
    candidates: list[tuple[int, int, re.Match[str], re.Match[str], str]] = []
    for verb_match in _ACTIVE_VERB_FORM.finditer(text):
        subject = _clean_surface(text[: verb_match.start()])
        if not subject:
            continue
        complement = text[verb_match.end() :]
        if _ACTIVE_COMPLEMENT_START.match(complement) is None:
            continue
        object_boundary = _choose_object_boundary(complement)
        if object_boundary is None:
            continue
        object_marker, value = object_boundary
        first_prep_offset = verb_match.end() + object_marker.start()
        candidates.append((first_prep_offset, -verb_match.start(), verb_match, object_marker, value))
    if not candidates:
        return None

    _prep_offset, _reverse_verb_offset, verb_match, object_marker, value = min(candidates)
    subject = _clean_surface(text[: verb_match.start()])
    tail = text[verb_match.end() :]
    predicate = _clean_surface(f"{verb_match.group()} {tail[:object_marker.end()]}")
    if not subject or not predicate or not value:
        return None
    return _FactProposition(
        subject=subject,
        predicate=predicate,
        value=value,
        subject_key=f"{_normalize_key(subject)} {_normalize_key(predicate)}",
        extraction_rule="active_verb_prepositional_object",
    )


def _extract_unmapped_raw_predicate(statement: str) -> str | None:
    """Retain a surface relation when its semantics are not safe to canonicalize."""
    match = _POSSESSIVE_COPULA_RAW_RELATION.fullmatch(statement.strip())
    if match is None:
        return None
    return _clean_surface(match.group("predicate")) or None


def _looks_like_entity_phrase(value: str) -> bool:
    """Recognize likely entity/object starts without a domain vocabulary."""
    tokens = value.split()
    if not tokens:
        return False
    first_index = 1 if tokens[0].casefold().strip("\"'([{,") in _ARTICLES else 0
    if first_index >= len(tokens):
        return False
    first = tokens[first_index].strip("\"'([{,")
    return bool(first) and (first[0].isupper() or first[0].isdigit())


def _clean_surface(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if cleaned.endswith(".."):
        cleaned = cleaned[:-1]
    elif cleaned.endswith((".", "!", "?", ";", ",")):
        cleaned = cleaned[:-1]
    return cleaned.strip()


def _normalize_key(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" .!?\t\r\n")
