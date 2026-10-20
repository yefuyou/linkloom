"""Bounded, benchmark-only preparation and scoring for the G3 pilot.

The input adapter remains Gold-blind.  This module exposes the separate Gold
reader only behind verification of a sealed provider-output artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import string
from time import perf_counter
from typing import Any, Iterable

from linkloom.context import ContextAssembler, ContextBudget, ContextBundle
from linkloom.decision_memory.models import DecisionMemoryState, DecisionStatus
from linkloom.indexing.bm25 import BM25Index
from linkloom.retrieval_v2.models import RetrievedEvidence

from .adapter import BenchmarkQuestion, FactConsolidationCase
from .memory import TemporalMemoryFixture, search_temporal_memory


MODEL = "gemini-3.8-flash"
METHOD_FLAT_RETRIEVAL = "Flat Retrieval"
METHOD_TEMPORAL_MEMORY = "LinkLoom Temporal Memory"
G3_METHODS = (METHOD_FLAT_RETRIEVAL, METHOD_TEMPORAL_MEMORY)
MAX_QUESTIONS = 10
MAX_GENERATION_CALLS = MAX_QUESTIONS * len(G3_METHODS)
MAX_TOTAL_GENERATION_ATTEMPTS = 30
PER_REQUEST_INPUT_TOKEN_CAP = 10_000
PER_REQUEST_OUTPUT_TOKEN_CAP = 512
MAX_TOTAL_INPUT_TOKENS = MAX_GENERATION_CALLS * PER_REQUEST_INPUT_TOKEN_CAP
MAX_TOTAL_OUTPUT_TOKENS = MAX_GENERATION_CALLS * PER_REQUEST_OUTPUT_TOKEN_CAP
MAX_TOTAL_ATTEMPT_INPUT_TOKENS = MAX_TOTAL_GENERATION_ATTEMPTS * PER_REQUEST_INPUT_TOKEN_CAP
MAX_TOTAL_ATTEMPT_OUTPUT_TOKENS = MAX_TOTAL_GENERATION_ATTEMPTS * PER_REQUEST_OUTPUT_TOKEN_CAP
MAX_TOTAL_BENCHMARK_COST_USD = 0.50
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 3.75
TOKEN_COUNTING_INPUT_CHARGED_CONSERVATIVELY = True

READER_SYSTEM_INSTRUCTION = (
    "Answer the MemoryAgentBench FactConsolidation question using only the "
    "provided facts. Facts are numbered in source order; when statements "
    "conflict, a larger serial number is newer. Return only the answer, with "
    "no explanation. If the provided context does not contain the answer, "
    "say I don't know."
)

_GOLD_COLUMNS = ["answers", "metadata.qa_pair_ids", "metadata.source"]
_ARTICLES = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION_TRANSLATION = str.maketrans("", "", string.punctuation)


@dataclass(frozen=True, slots=True)
class PreparedMethodContext:
    case_id: str
    workspace_id: str
    question_id: str
    question: str
    method: str
    context_bundle: ContextBundle
    retrieval_latency_ms: float
    memory_lookup_latency_ms: float
    context_assembly_latency_ms: float
    retrieval_evidence_ids: tuple[str, ...]
    memory_observation: dict[str, Any] | None

    def to_artifact(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "workspace_id": self.workspace_id,
            "question_id": self.question_id,
            "question": self.question,
            "method": self.method,
            "context_items_selected": [
                {
                    "item_id": item.item_id,
                    "source_type": item.source_type.value,
                    "estimated_tokens": item.estimated_tokens,
                    "source_refs": list(item.source_refs),
                    "text": item.text,
                }
                for item in self.context_bundle.selected
            ],
            "context_items_dropped": [
                {
                    "item_id": item.item_id,
                    "source_type": item.source_type.value,
                    "reason": item.reason.value,
                    "estimated_tokens": item.estimated_tokens,
                }
                for item in self.context_bundle.dropped
            ],
            "context_tokens_estimated": self.context_bundle.estimated_tokens,
            "context_text": self.context_bundle.rendered_text,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "memory_lookup_latency_ms": self.memory_lookup_latency_ms,
            "context_assembly_latency_ms": self.context_assembly_latency_ms,
            "retrieval_evidence_ids": list(self.retrieval_evidence_ids),
            "memory_observation": self.memory_observation,
            "memory_lookup_status": (
                "NOT_APPLICABLE"
                if self.method == METHOD_FLAT_RETRIEVAL
                else "HIT" if self.memory_observation is not None else "MISS"
            ),
        }


def static_worst_case_cost_usd(
    *,
    generation_attempts: int | None = None,
    count_tokens_requests: int | None = None,
    max_generation_attempts: int = MAX_TOTAL_GENERATION_ATTEMPTS,
) -> float:
    """Conservatively charge each countTokens and generation input request."""
    if generation_attempts is None:
        generation_attempts = MAX_GENERATION_CALLS
    if count_tokens_requests is None:
        count_tokens_requests = generation_attempts
    if (
        isinstance(generation_attempts, bool)
        or not isinstance(generation_attempts, int)
        or isinstance(max_generation_attempts, bool)
        or not isinstance(max_generation_attempts, int)
        or max_generation_attempts < 0
        or not 0 <= generation_attempts <= max_generation_attempts
        or isinstance(count_tokens_requests, bool)
        or not isinstance(count_tokens_requests, int)
        or not 0 <= count_tokens_requests <= max_generation_attempts
    ):
        raise ValueError("attempt counts must be within the G3 hard limit")
    input_tokens = (
        generation_attempts + count_tokens_requests
    ) * PER_REQUEST_INPUT_TOKEN_CAP
    return (
        input_tokens * INPUT_USD_PER_MILLION / 1_000_000
        + generation_attempts
        * PER_REQUEST_OUTPUT_TOKEN_CAP
        * OUTPUT_USD_PER_MILLION
        / 1_000_000
    )


def provider_failure_limit_exceeded(
    failures: int,
    *,
    planned_calls: int = MAX_GENERATION_CALLS,
) -> bool:
    """Stop once failures exceed 10% of the frozen method-case plan."""
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        raise ValueError("failures must be a non-negative integer")
    if isinstance(planned_calls, bool) or not isinstance(planned_calls, int) or planned_calls <= 0:
        raise ValueError("planned_calls must be a positive integer")
    return failures > planned_calls * 0.10


def build_generation_request(context_text: str) -> dict[str, Any]:
    if not isinstance(context_text, str) or not context_text.strip():
        raise ValueError("context_text must not be empty")
    return {
        "model": MODEL,
        "contents": [{"role": "user", "parts": [{"text": context_text}]}],
        "config": {
            "system_instruction": READER_SYSTEM_INSTRUCTION,
            "temperature": 0.0,
            "max_output_tokens": PER_REQUEST_OUTPUT_TOKEN_CAP,
            "automatic_function_calling": {"disable": True},
        },
    }


def prepare_method_context(
    case: FactConsolidationCase,
    flat_index: BM25Index,
    question: BenchmarkQuestion,
    *,
    method: str,
    memory: TemporalMemoryFixture | None = None,
    context_budget: ContextBudget | None = None,
) -> PreparedMethodContext:
    """Build one fair, bounded method context through product ContextAssembler.

    Temporal memory is navigation-only: its authorized current lookup selects
    the exact source fact, then ContextAssembler prioritizes that evidence and
    drops the redundant memory hint.  The model therefore receives source text,
    not a memory record standing in for evidence.
    """
    if method not in G3_METHODS:
        raise ValueError(f"unsupported G3 method: {method}")
    if question not in case.questions:
        raise ValueError("question is not part of the frozen input subset")
    if flat_index.workspace_ids() != {case.workspace_id}:
        raise ValueError("retrieval index is not isolated to the benchmark workspace")

    budget = context_budget or ContextBudget(
        max_evidence=5,
        max_memory=1,
        max_experience=0,
        max_chars=12_000,
        max_tokens=3_000,
    )
    retrieved: tuple[RetrievedEvidence, ...]
    lookup_latency_ms = 0.0
    memory_observation: dict[str, Any] | None = None
    evidence_ids: tuple[str, ...]

    if method == METHOD_FLAT_RETRIEVAL:
        retrieval_started = perf_counter()
        hits = flat_index.search(
            question.question,
            workspace_id=case.workspace_id,
            top_k=5,
        )
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000.0
        retrieved = tuple(
            _to_retrieved_evidence(case, flat_index, hit, rank=index + 1)
            for index, hit in enumerate(hits)
        )
        evidence_ids = tuple(item.evidence_id for item in retrieved)
        decision_memory = ()
        decision_states: dict[str, str] = {}
    else:
        if memory is None:
            raise ValueError("temporal memory fixture is required for this method")
        if memory.workspace_id != case.workspace_id:
            raise ValueError("temporal memory fixture crossed benchmark workspaces")
        lookup_started = perf_counter()
        query_result = search_temporal_memory(
            memory,
            question.question,
            subject_limit=1,
            result_limit=1,
        )
        lookup_latency_ms = (perf_counter() - lookup_started) * 1000.0
        if not query_result.values:
            # A miss is an observable outcome of this method, not a fixture
            # construction error. Preserve the frozen memory-only path: do not
            # silently fall back to Flat Retrieval, and let the reader see the
            # question without unsupported evidence.
            assembly_started = perf_counter()
            bundle = ContextAssembler(budget=budget).assemble(
                workspace_id=case.workspace_id,
                query=question.question,
                retrieved_evidence=(),
                decision_memory=(),
                temporal_query=False,
                decision_memory_states={},
            )
            context_assembly_latency_ms = (perf_counter() - assembly_started) * 1000.0
            if bundle.workspace_id != case.workspace_id or bundle.estimated_tokens > budget.max_tokens:
                raise ValueError("assembled context exceeded its workspace or token budget")
            return PreparedMethodContext(
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                question_id=question.question_id,
                question=question.question,
                method=method,
                context_bundle=bundle,
                retrieval_latency_ms=0.0,
                memory_lookup_latency_ms=lookup_latency_ms,
                context_assembly_latency_ms=context_assembly_latency_ms,
                retrieval_evidence_ids=(),
                memory_observation=None,
            )
        if len(query_result.values) != 1:
            raise ValueError("qualified temporal lookup did not return one current record")
        value = query_result.values[0]
        if (
            value.get("status") != "CURRENT"
            or value.get("workspace_id") != case.workspace_id
            or not isinstance(value.get("source_evidence_refs"), list)
            or len(value["source_evidence_refs"]) != 1
        ):
            raise ValueError("temporal lookup returned invalid current-state provenance")
        decision_id = value.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            raise ValueError("temporal lookup did not return a decision identity")
        decision = memory.store.get_decision(case.workspace_id, decision_id)
        if (
            decision is None
            or decision.workspace_id != case.workspace_id
            or decision.status is not DecisionStatus.CURRENT
            or decision.valid_to is not None
            or decision.memory_state is not DecisionMemoryState.ACTIVE
        ):
            raise ValueError("temporal decision failed active/current workspace validation")

        source_ref = value["source_evidence_refs"][0]
        retrieval_started = perf_counter()
        source_document = flat_index.get_document(case.workspace_id, source_ref)
        fact_by_id = {fact.fact_id: fact for fact in case.facts}
        source_fact = fact_by_id.get(source_ref)
        if (
            source_document is None
            or source_fact is None
            or source_document.evidence_id != source_ref
            or source_document.source_ref != source_ref
            or source_document.content != source_fact.statement
            or source_fact.subject_key != decision.subject_key
            or source_fact.value != decision.value
        ):
            raise ValueError("temporal source ref did not read back to the same fact identity")
        latest_fact = max(
            (
                fact for fact in case.facts
                if fact.subject_key == decision.subject_key
            ),
            key=lambda fact: fact.ordinal,
        )
        if latest_fact.fact_id != source_ref:
            raise ValueError("temporal current memory is not the latest ordered source fact")
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000.0
        retrieved = (
            RetrievedEvidence(
                evidence_id=source_document.evidence_id,
                source_ref=source_document.source_ref,
                logical_path=source_document.logical_path,
                score=1.0,
                rank=1,
                retrieval_channel="temporal_memory_source_readback",
                resource_id=source_document.resource_id,
                metadata={
                    **source_document.metadata,
                    "workspace_id": case.workspace_id,
                    "text": f"{source_fact.ordinal}. {source_document.content}",
                    "ordinal": source_fact.ordinal,
                },
            ),
        )
        evidence_ids = (source_document.evidence_id,)
        decision_memory = (decision,)
        decision_states = {decision.decision_id: DecisionMemoryState.ACTIVE.value}
        memory_observation = {
            "decision_id": decision.decision_id,
            "workspace_id": decision.workspace_id,
            "subject_key": decision.subject_key,
            "value": decision.value,
            "status": value["status"],
            "memory_state": decision.memory_state.value,
            "source_evidence_refs": list(decision.source_evidence_refs),
            "source_identity_verified": True,
            "latest_ordered_fact": latest_fact.ordinal,
        }

    assembly_started = perf_counter()
    bundle = ContextAssembler(budget=budget).assemble(
        workspace_id=case.workspace_id,
        query=question.question,
        retrieved_evidence=retrieved,
        decision_memory=decision_memory,
        temporal_query=False,
        decision_memory_states=decision_states,
    )
    context_assembly_latency_ms = (perf_counter() - assembly_started) * 1000.0

    if bundle.workspace_id != case.workspace_id or bundle.estimated_tokens > budget.max_tokens:
        raise ValueError("assembled context exceeded its workspace or token budget")
    selected_evidence_ids = {
        item.item_id for item in bundle.selected if item.source_type.value == "evidence"
    }
    if not selected_evidence_ids:
        raise ValueError("ContextAssembler selected no source evidence")
    if method == METHOD_TEMPORAL_MEMORY:
        if selected_evidence_ids != {evidence_ids[0]}:
            raise ValueError("ContextAssembler changed temporal source identity")
        duplicate_drop = next(
            (
                item for item in bundle.dropped
                if item.source_type.value == "decision_memory"
                and item.reason.value == "DUPLICATE"
            ),
            None,
        )
        if duplicate_drop is None:
            raise ValueError("ContextAssembler did not preserve the source-over-memory rule")

    return PreparedMethodContext(
        case_id=case.case_id,
        workspace_id=case.workspace_id,
        question_id=question.question_id,
        question=question.question,
        method=method,
        context_bundle=bundle,
        retrieval_latency_ms=retrieval_latency_ms,
        memory_lookup_latency_ms=lookup_latency_ms,
        context_assembly_latency_ms=context_assembly_latency_ms,
        retrieval_evidence_ids=evidence_ids,
        memory_observation=memory_observation,
    )


def _to_retrieved_evidence(
    case: FactConsolidationCase,
    flat_index: BM25Index,
    hit: Any,
    *,
    rank: int,
) -> RetrievedEvidence:
    document = flat_index.get_document(case.workspace_id, hit.resource_id)
    if (
        document is None
        or document.evidence_id != hit.evidence_id
        or document.source_ref != hit.source_ref
        or document.workspace_id != case.workspace_id
    ):
        raise ValueError("BM25 evidence identity did not read back to its source")
    fact = next((item for item in case.facts if item.fact_id == hit.source_ref), None)
    if fact is None or fact.statement != document.content:
        raise ValueError("BM25 source ref does not identify a dataset fact")
    return RetrievedEvidence(
        evidence_id=hit.evidence_id,
        source_ref=hit.source_ref,
        logical_path=hit.logical_path,
        score=hit.score,
        rank=rank,
        retrieval_channel="bm25",
        resource_id=hit.resource_id,
        metadata={
            **hit.metadata,
            "workspace_id": case.workspace_id,
            "text": f"{fact.ordinal}. {document.content}",
            "ordinal": fact.ordinal,
        },
    )


def normalize_official_answer(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("official metric accepts string answers")
    normalized = value.casefold().translate(_PUNCTUATION_TRANSLATION)
    normalized = _ARTICLES.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def score_official_substring_exact_match(
    prediction: str,
    gold_answers: Iterable[str],
) -> bool:
    normalized_prediction = normalize_official_answer(prediction)
    if not normalized_prediction:
        return False
    normalized_gold = [normalize_official_answer(answer) for answer in gold_answers]
    if not normalized_gold or any(not answer for answer in normalized_gold):
        raise ValueError("official metric requires non-empty Gold answer variants")
    return any(answer in normalized_prediction for answer in normalized_gold)


def verify_frozen_output_artifact(output_path: str | Path, seal_path: str | Path) -> bool:
    import hashlib
    import json

    output = Path(output_path)
    seal = Path(seal_path)
    if not output.is_file() or not seal.is_file():
        return False
    try:
        marker = json.loads(seal.read_text(encoding="utf-8"))
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
    except (OSError, ValueError, TypeError):
        return False
    return marker.get("sealed") is True and marker.get("sha256") == digest


def load_official_gold_answers(
    parquet_path: str | Path,
    question_ids: list[str] | tuple[str, ...],
    *,
    provider_outputs_path: str | Path,
    provider_outputs_seal_path: str | Path,
    parquet_reader: Any | None = None,
) -> dict[str, tuple[str, ...]]:
    """Read official answer variants only after generation outputs are frozen."""
    if not verify_frozen_output_artifact(provider_outputs_path, provider_outputs_seal_path):
        raise ValueError("Gold is unavailable until a sealed provider output artifact exists")
    if parquet_reader is None:
        try:
            import pyarrow.parquet as parquet_reader
        except ImportError as error:  # pragma: no cover - environment-specific
            raise RuntimeError("Gold scoring requires the benchmark Parquet reader") from error
    table = parquet_reader.read_table(str(parquet_path), columns=_GOLD_COLUMNS)
    rows = table.to_pylist()
    matches = [row for row in rows if row.get("source") == "factconsolidation_sh_6k"]
    if len(matches) != 1:
        raise ValueError("official scoring row is missing or ambiguous")
    row = matches[0]
    ids = row.get("qa_pair_ids")
    answers = row.get("answers")
    if not isinstance(ids, list) or not isinstance(answers, list) or len(ids) != len(answers):
        raise ValueError("official answer variants do not align with QA identifiers")
    by_id: dict[str, tuple[str, ...]] = {}
    for question_id, variants in zip(ids, answers, strict=True):
        if (
            not isinstance(question_id, str)
            or not isinstance(variants, list)
            or any(not isinstance(value, str) or not value.strip() for value in variants)
        ):
            raise ValueError("official answer variants have an invalid schema")
        by_id[question_id] = tuple(variants)
    missing = [question_id for question_id in question_ids if question_id not in by_id]
    if missing:
        raise ValueError("official score row is missing selected QA identifiers")
    return {question_id: by_id[question_id] for question_id in question_ids}
