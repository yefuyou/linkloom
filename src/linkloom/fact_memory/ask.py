"""Answer a question from reviewed fact records only.

Retrieval is lexical (BM25 with a positive idf) over single records. The host
takes the subject(s) of the best-matching records and offers every relevant
record of those subjects that was stated at or before the question date,
sorted by effective time. Merging at intake stays strict (no synonyms), so two
attribute names for one thing reach the model side by side with their times.
With a configured model the answer is one bounded call that may only cite the
offered ids; without one the host returns the newest of the closest matches.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
import re
from typing import Any, Callable
from uuid import uuid4

from linkloom.fact_memory.normalize import fold
from linkloom.fact_memory.store import FactRecord

MAX_RECORDS = 40
SUBJECT_SEEDS = 3        # subjects of the top-N records are the question's subjects
NEAR_BEST = 0.8          # deterministic answer: newest record scoring >= 80% of the best
_STOP = frozenset("""a an the i me my you your is are was were be been do did does have has had of to in on at for
with and or what which who whom when where how many much did do does it its that this there their they them we our
can could would should will shall about from by as than then so if not no yes""".split())
PROMPT = """You answer one question using ONLY the reviewed fact records below.
Question date: {as_of}. Records are sorted by effective_at, the time their source stated them; none is later
than the question date. Several records may describe the same thing under different attribute names.
Rules: when the question names no point in time, the most recent record wins. When the question asks about a past
point in time, use only records at or before that point. You may compute counts, sums or date differences from
the records and their times. If the records do not answer the question, use status UNKNOWN.
Reply with JSON only: {{"status": "ANSWERED" or "UNKNOWN", "answer": "<short answer>", "fact_ids": ["F1", ...]}}
fact_ids must list every record the answer relies on.

Question: {question}
Records: {records}"""


def _stem(token: str) -> str:
    """Light English suffix folding for retrieval only (never for intake keys)."""
    if not token.isalpha() or not token.isascii() or len(token) <= 4:
        return token
    for suffix, replacement, shortest in (("ies", "y", 5), ("sses", "ss", 6), ("ing", "", 6), ("ed", "", 5)):
        if token.endswith(suffix) and len(token) >= shortest:
            return token[: -len(suffix)] + replacement
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    return [_stem(token) for token in re.findall(r"[a-z0-9]+|[一-鿿]", fold(text))
            if token not in _STOP]


def relevant_records(question: str, records: tuple[FactRecord, ...], as_of: datetime) -> list[tuple[FactRecord, float]]:
    """Relevant records of the question's subject(s), oldest first, with their scores."""
    usable = [record for record in records if record.effective_at <= as_of]
    query = tokens(question)
    if not usable or not query:
        return []
    documents = [tokens(" ".join([record.attribute_name] * 2 + [record.attribute_key]
                                 + [record.value_text] * 2 + [record.evidence_sentence])) for record in usable]
    scores = _bm25(documents, query)
    ranked = sorted((index for index in range(len(usable)) if scores[index] > 0),
                    key=lambda index: (-scores[index], usable[index].fact_id))
    if not ranked:
        return []
    subjects = list(dict.fromkeys(usable[index].subject_id for index in ranked[:SUBJECT_SEEDS]))
    chosen = [index for index in ranked if usable[index].subject_id in subjects][:MAX_RECORDS]
    chosen.sort(key=lambda index: (usable[index].effective_at, usable[index].recorded_at, usable[index].fact_id))
    return [(usable[index], scores[index]) for index in chosen]


def _bm25(documents: list[list[str]], query: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    # Lucene-style idf stays positive, so a workspace with one slot still matches.
    count = len(documents)
    average = sum(len(document) for document in documents) / count or 1.0
    frequency = Counter(token for document in documents for token in set(document))
    scores = []
    for document in documents:
        terms = Counter(document)
        score = 0.0
        for token in set(query):
            if terms[token]:
                idf = math.log(1 + (count - frequency[token] + 0.5) / (frequency[token] + 0.5))
                score += idf * terms[token] * (k1 + 1) / (terms[token] + k1 * (1 - b + b * len(document) / average))
        scores.append(score)
    return scores


def answer(question: str, as_of: datetime, records: tuple[FactRecord, ...],
           adapter_factory: Callable[[], Any] | None) -> dict[str, Any]:
    """Return {status, answer, cited (records), offered (count), code}."""
    selected = relevant_records(question, records, as_of)
    if not selected:
        return {"status": "NO_MATCH", "code": "FACT_SCOPE_NO_MATCH", "cited": [], "offered": 0}
    offered = {f"F{index + 1}": record for index, (record, _) in enumerate(selected)}
    if adapter_factory is None:
        best = max(score for _, score in selected)
        newest = [record for record, score in selected if score >= NEAR_BEST * best][-1]
        return {"status": "ANSWERED", "code": "FACT_DETERMINISTIC", "answer": newest.value_text,
                "cited": [newest], "offered": len(offered)}
    payload = [{"id": identifier, "subject": record.subject_label, "attribute": record.attribute_name,
                "value": record.value_text, "effective_at": record.effective_at.isoformat(),
                "source_sentence": record.evidence_sentence[:600]} for identifier, record in offered.items()]
    prompt = PROMPT.format(as_of=as_of.isoformat(), question=question,
                           records=json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
    identifier = "fact-answer-" + uuid4().hex
    request = ModelTurnRequest(run_id=identifier, task_id=identifier, turn_id=identifier, agent_id="fact-answer",
                               sequence=1, user_input=prompt, observation=None, available_tools=[],
                               generation_options=ModelGenerationOptions(max_output_tokens=400, temperature=0.0))
    try:
        response = adapter_factory().complete(request)
    except Exception:
        return {"status": "FAILED", "code": "FACT_ANSWER_PROVIDER_FAILED", "cited": [], "offered": len(offered)}
    action = getattr(response, "action", None)
    if getattr(response, "error", None) is not None or action is None or action.kind != "final":
        return {"status": "FAILED", "code": "FACT_ANSWER_PROVIDER_FAILED", "cited": [], "offered": len(offered)}
    parsed = _parse(action.final_answer, offered)
    if parsed is None:
        return {"status": "FAILED", "code": "FACT_ANSWER_INVALID", "cited": [], "offered": len(offered)}
    return {**parsed, "offered": len(offered)}


def _parse(text: str, offered: dict[str, FactRecord]) -> dict[str, Any] | None:
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in {"ANSWERED", "UNKNOWN"}:
        return None
    if value["status"] == "UNKNOWN":
        return {"status": "UNKNOWN", "code": "FACT_RECORDS_INSUFFICIENT", "cited": []}
    ids = value.get("fact_ids")
    result = value.get("answer")
    if (not isinstance(result, str) or not result.strip() or len(result) > 500 or not isinstance(ids, list)
            or not ids or any(not isinstance(item, str) or item not in offered for item in ids)):
        return None
    return {"status": "ANSWERED", "code": "FACT_MODEL_ANSWER", "answer": result.strip(),
            "cited": [offered[item] for item in dict.fromkeys(ids)]}
