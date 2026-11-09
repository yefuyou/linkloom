"""Answer a question from reviewed fact records only.

Retrieval is lexical (BM25, positive idf) over slot text. With a configured model the
answer is one bounded call that may only cite the offered fact ids; without
one the host returns the best slot's value in force at the question time.
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
from linkloom.fact_memory.store import FactRecord, value_at

MAX_SLOTS = 8
MAX_HISTORY = 6
_STOP = frozenset("""a an the i me my you your is are was were be been do did does have has had of to in on at for
with and or what which who whom when where how many much did do does it its that this there their they them we our
can could would should will shall about from by as than then so if not no yes""".split())
PROMPT = """You answer one question using ONLY the reviewed fact records below.
Each record has an id, subject, attribute, value, effective_at (the time its source stated it) and status:
CURRENT, SUPERSEDED (a later record for the same subject and attribute replaced it) or NOT_YET (stated after the
question date). Question date: {as_of}. Prefer the value in force at the question date; you may compute counts,
sums or date differences from the records. If the records do not answer the question, use status UNKNOWN.
Reply with JSON only: {{"status": "ANSWERED" or "UNKNOWN", "answer": "<short answer>", "fact_ids": ["F1", ...]}}
fact_ids must list every record the answer relies on.

Question: {question}
Records: {records}"""


def tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+|[一-鿿]", fold(text)) if token not in _STOP]


def select_slots(question: str, records: tuple[FactRecord, ...]) -> list[list[FactRecord]]:
    slots: dict[str, list[FactRecord]] = {}
    for record in records:
        slots.setdefault(record.slot_key, []).append(record)
    if not slots:
        return []
    groups = list(slots.values())
    documents = [tokens(" ".join(
        [group[0].attribute_key] * 2 + [record.attribute_name for record in group]
        + [record.value_text for record in group] * 2 + [record.quote for record in group]))
        for group in groups]
    query = tokens(question)
    if not query:
        return []
    scores = _bm25(documents, query)
    ranked = sorted(range(len(groups)), key=lambda index: (-scores[index], groups[index][0].slot_key))
    return [groups[index] for index in ranked[:MAX_SLOTS] if scores[index] > 0]


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


def offered_records(slots: list[list[FactRecord]], as_of: datetime) -> list[dict[str, Any]]:
    offered = []
    for group in slots:
        current = value_at(group, as_of)
        for record in group[-MAX_HISTORY:]:
            status = ("NOT_YET" if record.effective_at > as_of else
                      "CURRENT" if current is not None and (record.fact_id == current.fact_id
                                                             or record.corroborates_fact_id == current.fact_id)
                      else "SUPERSEDED")
            offered.append({"id": f"F{len(offered) + 1}", "fact": record, "status": status})
    return offered


def answer(question: str, as_of: datetime, records: tuple[FactRecord, ...],
           adapter_factory: Callable[[], Any] | None) -> dict[str, Any]:
    """Return {status, answer, cited (records), offered (count), code}."""
    slots = select_slots(question, records)
    if not slots:
        return {"status": "NO_MATCH", "code": "FACT_SCOPE_NO_MATCH", "cited": [], "offered": 0}
    offered = offered_records(slots, as_of)
    if adapter_factory is None:
        best = value_at(slots[0], as_of)
        if best is None:
            return {"status": "UNKNOWN", "code": "FACT_NOT_IN_FORCE", "cited": [], "offered": len(offered)}
        return {"status": "ANSWERED", "code": "FACT_DETERMINISTIC", "answer": best.value_text,
                "cited": [best], "offered": len(offered)}
    payload = [{"id": item["id"], "subject": item["fact"].subject_label, "attribute": item["fact"].attribute_name,
                "value": item["fact"].value_text, "effective_at": item["fact"].effective_at.isoformat(),
                "status": item["status"], "source_quote": item["fact"].quote[:600]} for item in offered]
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
    parsed = _parse(action.final_answer, {item["id"]: item["fact"] for item in offered})
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
