#!/usr/bin/env python3
"""Routing evaluation (offline by default): python -B scripts/eval_routing.py --details all.

Uses the production route_question pipeline; --compare REV loads original routing from Git. This measures
routing components, not ingestion, HTTP/UI or answers.
--retrieval bm25/hybrid compares lexical-only with the installed cached local model.
Real calls require --live-provider and a new --llm-run-dir; no implicit retries.
Offline mode never initializes a provider. Default offline output goes to stdout.

FIXTURE is a synthetic projection of UnifiedAskService._trusted_slots. Launch's
supplier values and budget follow DecisionDiscoveryFixture; cloud/database
histories extend that fixture. Only four reviewed Launch slots are visible.
Research is proposal-only; Support belongs to another workspace. Neither is in
this trusted index. Aliases are fixed workspace data supplied to the existing
relation resolver. No matching or date-resolution rules are added here.

Expected routes use the proposed contract. Legacy AMBIGUOUS -> CLARIFY,
NO_MATCH -> NO_MATCH, DECISION -> CURRENT/AS_OF only when the existing date parser
resolves a point. UNKNOWN remains UNKNOWN. Scope and date failures take precedence
as in UnifiedAskService.ask. Relative periods, ranges and invalid dates require
CLARIFY; no reference day is invented. Date clarification can retain a known slot;
topic clarification uses slot=null. A rejected wording is never relabelled from
NO_MATCH to CLARIFY to improve the score. Predictions never inspect expectations.

Primary accuracy is exact (route, slot), including null slots. Separate metrics
cover intent, date parsing, resolved slots, and route/slot/date together. Raw
legacy intent, discovery reason/candidates, date errors and reproducibility
hashes remain available in JSON. This is a development baseline, not held-out
generalization evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from urllib.request import Request, build_opener, HTTPRedirectHandler
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "routing" / "cases.jsonl"
# Avoid an editable install silently importing the old release checkout.
sys.path.insert(0, str(ROOT / "src"))

from linkloom.agents.model_adapter import ModelProviderError, ModelResponse
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui import scope_discovery, unified_ask, question_routing
from linkloom.ui.question_routing import route_question

ROUTES = ("CURRENT", "AS_OF", "INVESTIGATION", "CLARIFY", "NO_MATCH")
INTENTS = ROUTES[:3]
REQUIRED_QUERIES = {
    "我们现在用哪家云厂商？", "目前选定的数据库是哪个？",
    "What cloud provider did we pick?",
    "Who decided to switch vendors and when?", "上周会议定了什么？",
}
FIXTURE = {
    "workspace_id": "routing-launch-v1",
    "description": "Synthetic reviewed-slot projection, not a real notebook/workspace",
    "excluded": {
        "Research": "pending supplier proposal only; not reviewed",
        "Support": "reviewed supplier Riverton in another workspace",
    },
    "slots": [
        {"slot_id": "launch.cloud", "subject": "Launch", "relation": "uses cloud provider",
         "values": ["AWS", "Azure"], "current_value": "Azure",
         "decision_ids": ["cloud-20261001", "cloud-20261007"],
         "valid_from": ["2026-10-01T10:00:00Z", "2026-10-07T10:00:00Z"]},
        {"slot_id": "launch.database", "subject": "Launch", "relation": "uses database",
         "values": ["MySQL", "PostgreSQL"], "current_value": "PostgreSQL",
         "decision_ids": ["database-20261001", "database-20261007"],
         "valid_from": ["2026-10-01T10:00:00Z", "2026-10-07T10:00:00Z"]},
        {"slot_id": "launch.supplier", "subject": "Launch", "relation": "uses supplier",
         "values": ["Birchline", "Wrenwell"], "current_value": "Wrenwell",
         "decision_ids": ["supplier-20261001", "supplier-20261007"],
         "valid_from": ["2026-10-01T10:00:00Z", "2026-10-07T10:00:00Z"]},
        {"slot_id": "launch.budget", "subject": "Launch", "relation": "has budget",
         "values": ["40000", "50000"], "current_value": "50000",
         "decision_ids": ["budget-20261001", "budget-20261002"],
         "valid_from": ["2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z"]},
    ],
    "relation_aliases": {
        "cloud provider": "uses cloud provider", "cloud vendor": "uses cloud provider",
        "cloud platform": "uses cloud provider", "云厂商": "uses cloud provider",
        "云服务商": "uses cloud provider", "云平台": "uses cloud provider",
        "database": "uses database", "DBMS": "uses database",
        "数据库": "uses database", "数据库引擎": "uses database",
        "合作方": "uses supplier", "供应商": "uses supplier", "供货商": "uses supplier",
        "供应方": "uses supplier", "budget": "has budget",
        "预算": "has budget", "经费": "has budget",
    },
}


# Standard paid Gemini pricing; DeepSeek uses peak, cache-miss rates (upper estimate).
# No tools, cache creation, batch, priority pricing, redirects or retry transports.
LIVE_PROVIDERS = {
    "gemini": ("gemini-3.8-flash", "GEMINI_API_KEY", Decimal("0.75"), Decimal("3.75")),
    "deepseek": ("deepseek-flash", "DEEPSEEK_API_KEY", Decimal("0.30"), Decimal("1.20")),
}
LIVE_MAX_REQUESTS = 100
LIVE_MAX_COST_USD = Decimal("1")
LIVE_PRICES_CHECKED = "2026-10-29"
LIVE_PRICE_VALID_THROUGH = "2026-12-31"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeepSeekClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def create_chat_completion(self, **payload):
        # One stdlib HTTP POST; no SDK retry or automatic tool execution.
        request = Request("https://api.deepseek.com/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            return json.load(response)


def build_live_adapter(provider):
    model, variable, _, _ = LIVE_PROVIDERS[provider]
    key = os.environ.get(variable)
    if not key:
        raise ValueError("Real provider requires " + variable + " in this process environment")
    if provider == "gemini":
        from linkloom.agents.providers.gemini_api import GeminiProviderAdapter, build_gemini_sdk_client
        from linkloom.ui.__main__ import GeminiSDKClientBridge
        client = build_gemini_sdk_client(key, http_options_kwargs=dict(
            timeout=30000, retry_options=dict(attempts=1), client_args=dict(follow_redirects=False)))
        return GeminiProviderAdapter(GeminiSDKClientBridge(client), model_id=model)
    from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
    return DeepSeekProviderAdapter(_DeepSeekClient(key), model_id=model)


def validate_selector_response(response, payload):
    """Replay only the captured response through the unchanged production validator.

    This is local validation, not a second provider request. Candidate IDs,
    original question and date context are exactly those of the real request.
    """
    class Replay:
        def complete(self, unused_request):
            return response
    candidates = [dict(slot_id=c["slot_id"], subject=c["text"], relation="")
                  for c in payload["candidates"]]
    try:
        return question_routing._select(Replay(), payload["query"], candidates,
            {"intent": payload["rule_intent"]}, payload["as_of"],
            date_unresolved=payload["date_unresolved"])
    except Exception:
        # The production caller likewise treats any selector validation error
        # (including ProductApplicationError for malformed dates) as invalid.
        return None


class LiveRoutingProvider:
    """Reserve before each complete; unknown/error outcomes stop the whole run.

    UTF-8 bytes plus framing bound input tokens conservatively for text-only
    requests. The unchanged selector caps generated tokens, including thinking.
    Unknown usage retains its entire reservation; it is never reported as free.
    """
    def __init__(self, provider, run_dir, *, max_requests=100, max_cost_usd=Decimal("1"), adapter=None):
        if type(max_requests) is not int or not 1 <= max_requests <= LIVE_MAX_REQUESTS:
            raise ValueError("max requests must be between 1 and 100")
        cost = Decimal(str(max_cost_usd))
        if not cost.is_finite() or not 0 < cost <= LIVE_MAX_COST_USD:
            raise ValueError("max cost must be positive and at most USD 1")
        if datetime.now(timezone.utc).date().isoformat() > LIVE_PRICE_VALID_THROUGH:
            raise ValueError("Provider price snapshot expired; verify prices before any real run")
        self.provider, self.model = provider, LIVE_PROVIDERS[provider][0]
        self.model_id = self.model  # preserve model identity at the selector seam
        self.input_rate, self.output_rate = LIVE_PROVIDERS[provider][2:]
        self.adapter = adapter if adapter is not None else build_live_adapter(provider)
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=False)  # never overwrite/replay a run
        self.max_requests, self.max_cost = max_requests, cost
        self.charged = Decimal("0")
        self.calls, self.failure, self.case_id = [], None, None
        self.save()

    def save(self):
        data = self.summary()
        try:
            with (self.run_dir / "calls.json").open("w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self.failure = self.failure or "JOURNAL_WRITE_FAILED"
            raise

    def summary(self):
        return dict(provider=self.provider, model=self.model, max_requests=self.max_requests,
            max_cost_usd=float(self.max_cost), requests=len(self.calls),
            cost_accounted_usd=float(self.charged), failure=self.failure,
            pricing=dict(checked=LIVE_PRICES_CHECKED, valid_through=LIVE_PRICE_VALID_THROUGH,
                input_usd_per_million=float(self.input_rate), output_usd_per_million=float(self.output_rate),
                basis="paid standard / DeepSeek peak cache-miss estimate; not a billing invoice"),
            calls=deepcopy(self.calls))

    def stop(self, reason):
        self.failure = self.failure or reason
        self.save()
        return ModelResponse(error=ModelProviderError(code="MODEL_CANCELLED", category="cancelled",
            message="Live routing evaluation stopped.", retryable=False, outcome="known_failure"))

    def complete(self, request):
        if self.failure:
            return self.stop(self.failure)
        # Only the production selector's whitelisted inference payload crosses
        # this boundary. No case, expectation, reason or scoring object is accepted.
        try:
            payload = json.loads(request.user_input.split("\n", 1)[1])
            if set(payload) != {"query", "rule_intent", "as_of", "date_unresolved", "candidates"}:
                raise ValueError("unexpected selector payload")
            if any(set(c) != {"slot_id", "text"} for c in payload["candidates"]):
                raise ValueError("unexpected candidate payload")
            if (request.available_tools or request.observation is not None or request.evidence_context
                    or request.tool_history or request.tool_turns or request.recovery_guidance
                    or request.provider_continuation or request.model_id is not None):
                raise ValueError("only text-only selector requests are permitted")
            output_bound = request.generation_options.max_output_tokens
            allowed_output = question_routing._selector_generation_options(self).max_output_tokens
            if type(output_bound) is not int or not 1 <= output_bound <= allowed_output:
                raise ValueError("unbounded selector output")
        except (ValueError, TypeError, IndexError, AttributeError):
            return self.stop("INVALID_INFERENCE_PAYLOAD")
        input_bound = len(json.dumps(request.to_dict(), ensure_ascii=False).encode("utf-8")) + 4096
        reservation = (input_bound * self.input_rate + output_bound * self.output_rate) / 1_000_000
        if len(self.calls) >= self.max_requests or self.charged + reservation > self.max_cost:
            return self.stop("BUDGET_EXHAUSTED")
        row = dict(sequence=len(self.calls) + 1, case_id=self.case_id,
            prompt_sha256=digest(request.user_input.encode("utf-8")), input_bound=input_bound,
            output_bound=output_bound, cost_reserved_usd=float(reservation),
            input_tokens=None, output_tokens=None, thinking_tokens=None, billable_output_tokens=None,
            cost_usd=None, cost_accounted_usd=float(reservation), outcome="RESERVED", response_text=None)
        self.calls.append(row)
        self.charged += reservation
        self.save()  # durable reservation before any network call
        try:
            response = self.adapter.complete(request)
        except Exception as exc:
            row.update(outcome="UNKNOWN", error_type=type(exc).__name__)
            return self.stop("PROVIDER_EXCEPTION")
        if not isinstance(response, ModelResponse):
            row["outcome"] = "UNKNOWN"
            return self.stop("INVALID_PROVIDER_RESPONSE")
        usage = response.usage
        row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens, provider_request_id=response.provider_request_id,
            provider_response_id=response.provider_response_id, finish_reason=response.finish_reason)
        if response.error is not None:
            row.update(error_code=response.error.code, error=response.error.to_dict())
        if response.action is not None and response.action.kind == "final":
            row["response_text"] = response.action.final_answer
        if all(type(v) is int for v in (usage.input_tokens, usage.output_tokens, usage.total_tokens)):
            billable = usage.total_tokens - usage.input_tokens
            if billable < usage.output_tokens:
                row["outcome"] = "UNKNOWN"
                return self.stop("INCONSISTENT_USAGE")
            actual = (usage.input_tokens * self.input_rate + billable * self.output_rate) / 1_000_000
            self.charged += actual - reservation
            row.update(billable_output_tokens=billable, thinking_tokens=billable - usage.output_tokens,
                cost_usd=float(actual), cost_accounted_usd=float(actual))
            if usage.input_tokens > input_bound or billable > output_bound or self.charged > self.max_cost:
                row["outcome"] = "USAGE_EXCEEDED_RESERVATION"
                return self.stop("USAGE_EXCEEDED_RESERVATION")
        else:
            row.update(outcome="FAILED" if response.error else "UNKNOWN", usage_unknown=True)
            return self.stop(response.error.code if response.error else "USAGE_UNAVAILABLE")
        if response.error is not None:
            row.update(outcome="FAILED", error_code=response.error.code)
            return self.stop(response.error.code)
        selection = validate_selector_response(response, payload)
        row.update(outcome="COMPLETED", selection_valid=selection is not None,
                   validated_selection=selection)
        self.save()
        return response


def llm_metrics(rows, live=None):
    called = [r for r in rows if r["actual"].get("llm_call_count", 0)]
    accepted = [r for r in called if r["actual"]["decided_by"] == "llm"]
    raw_wrong = nulls = correct_nulls = invalid = 0
    for row in called:
        for call in row["actual"].get("llm_calls", []):
            try:
                selection = json.loads(call["response_text"])
                if not isinstance(selection, dict) or set(selection) != {"intent", "slot_id", "as_of"}:
                    raise ValueError("invalid selection")
                chosen = selection["slot_id"]
                raw_wrong += chosen is not None and chosen != row["expected_slot"]
                valid = call.get("selection_valid", False)
                nulls += chosen is None and valid
                correct_nulls += chosen is None and valid and row["route_slot_date_correct"]
                invalid += not valid
            except (TypeError, ValueError, KeyError):
                invalid += 1
    vetoes = [r for r in rows if r["actual"].get("hard_veto")]
    return dict(calls=len(live.calls) if live else 0,
        evaluated_calls=sum(r["actual"]["llm_call_count"] for r in called),
        correct_disambiguations=sum(r["route_slot_date_correct"] for r in accepted),
        wrong_selections=raw_wrong,
        accepted_wrong_slots=sum(not r["slot_correct"] for r in accepted),
        accepted_disambiguations=len(accepted),
        incorrect_accepted_disambiguations=sum(not r["route_slot_date_correct"] for r in accepted),
        null_selections=nulls, correct_null_decisions=correct_nulls, invalid_outputs=invalid,
        hard_veto_cases=len(vetoes), hard_veto_llm_calls=sum(r["actual"]["llm_call_count"] for r in vetoes),
        hard_veto_overturned=sum(r["actual"]["route"] != "NO_MATCH" or r["actual"]["slot"] is not None for r in vetoes))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    slot_ids = {slot["slot_id"] for slot in FIXTURE["slots"]}
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"{path}:{line_number}: blank JSONL row")
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        required = {"id", "query", "language", "category", "expected_route", "expected_slot",
                    "expected_intent", "expected_as_of", "reason"}
        if not required <= item.keys():
            raise ValueError(f"{path}:{line_number}: missing {sorted(required - item.keys())}")
        for key in ("id", "query", "category", "reason"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"{path}:{line_number}: {key} must be nonempty text")
        if item["language"] not in {"zh", "en", "mixed"}:
            raise ValueError(f"{path}:{line_number}: invalid language")
        if item["expected_route"] not in ROUTES or item["expected_intent"] not in INTENTS:
            raise ValueError(f"{path}:{line_number}: invalid route/intent")
        if item["expected_slot"] is not None and item["expected_slot"] not in slot_ids:
            raise ValueError(f"{path}:{line_number}: unknown expected slot")
        if item["expected_route"] in INTENTS and item["expected_slot"] is None:
            raise ValueError(f"{path}:{line_number}: a resolved route needs a slot")
        if item["expected_route"] == "NO_MATCH" and item["expected_slot"] is not None:
            raise ValueError(f"{path}:{line_number}: NO_MATCH cannot select a slot")
        expected_date = item["expected_as_of"]
        if expected_date is not None:
            resolved, error = scope_discovery.resolve_query_date(expected_date, None)
            if error or resolved != expected_date:
                raise ValueError(f"{path}:{line_number}: invalid expected date")
        if item["expected_route"] == "AS_OF" and expected_date is None:
            raise ValueError(f"{path}:{line_number}: AS_OF needs an exact date")
        cases.append(item)
    if not cases:
        raise ValueError("expected at least one case")
    if path.resolve() == DEFAULT_CASES.resolve() and len(cases) != 60:
        raise ValueError(f"expected exactly 60 baseline cases, got {len(cases)}")
    for key in ("id", "query"):
        if len({case[key] for case in cases}) != len(cases):
            raise ValueError(f"duplicate {key}")
    if path.resolve() == DEFAULT_CASES.resolve():
        missing = REQUIRED_QUERIES - {case["query"] for case in cases}
        if missing:
            raise ValueError(f"missing required baseline queries: {sorted(missing)}")
    return cases


def load_legacy(revision: str) -> tuple:
    """Load the original two modules from Git without checkout or file writes."""
    modules = []
    for name in ("scope_discovery", "unified_ask"):
        relative = f"src/linkloom/ui/{name}.py"
        source = subprocess.check_output(
            ["git", "show", revision + ":" + relative], cwd=ROOT).decode("utf-8")
        module = types.ModuleType("routing_baseline_" + name)
        module.__file__ = str(ROOT / relative)
        module.__source_sha256__ = digest(source.encode("utf-8"))
        sys.modules[module.__name__] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        modules.append(module)
    return modules[1], modules[0]


def predict(query: str, slots: list[dict[str, Any]], resolver: FrozenRelationResolver, *,
            embedder: Any = scope_discovery.AUTO_EMBEDDER, provider: LiveRoutingProvider | None = None) -> dict[str, Any]:
    start = len(provider.calls) if provider else 0
    result = route_question(query, slots, resolver, workspace_id=FIXTURE["workspace_id"], embedder=embedder,
                            provider_factory=(lambda: provider) if provider else None)
    discovery = result["discovery"]
    by_scope = {(slot["subject"], slot["relation"]): slot["slot_id"] for slot in slots}
    scope = result["scope"]
    parsed_date, date_error = scope_discovery.resolve_query_date(query, None)
    return {
        "route": result["route"],
        "slot": by_scope[(scope["subject"], scope["relation"])] if scope else None,
        "as_of": result["as_of"], "intent": result["intent"],
        "raw_intent": result["intent"], "task_code": result["code"],
        "decided_by": result["decided_by"],
        "llm_attempted": result["llm_attempted"], "llm_error": result["llm_error"],
        "llm_call_count": len(provider.calls) - start if provider else 0,
        "llm_calls": deepcopy(provider.calls[start:]) if provider else [],
        "hard_veto": scope_discovery.entity_check(query, scope_discovery.workspace_snapshot(
            slots, resolver, FIXTURE["workspace_id"]))["hard_veto"],
        "discovery_status": discovery["status"], "discovery_reason": discovery["reason"],
        "candidates": [by_scope[(item["subject"], item["relation"])] for item in discovery["candidates"]],
        "parsed_date": parsed_date, "date_error": date_error,
        "raw_candidates": [by_scope[(item["subject"], item["relation"])]
                           for item in discovery["raw_candidates"]],
        "dense_candidate_count": sum(item.get("dense_score") is not None for item in discovery["candidates"]),
    }


def predict_legacy(query: str, slots: list[dict[str, Any]], resolver: FrozenRelationResolver, modules: tuple) -> dict[str, Any]:
    """Adapt current routing components without inspecting expected labels."""
    legacy_ask, legacy_scope = modules
    task = legacy_ask.classify_task(query)
    discovery = legacy_scope.discover_scope(query, slots, resolver)
    parsed_date, date_error = legacy_scope.resolve_query_date(query, None)
    by_scope = {(slot["subject"], slot["relation"]): slot["slot_id"] for slot in slots}
    scope = discovery.get("scope")
    selected = by_scope[(scope["subject"], scope["relation"])] if scope else None
    intent = task["intent"]
    if intent == "DECISION":
        intent = "AS_OF" if parsed_date is not None else "CURRENT"
    route = intent
    if scope is None:
        route = {"AMBIGUOUS": "CLARIFY", "NO_MATCH": "NO_MATCH"}[discovery["status"]]
    elif date_error:
        route = "CLARIFY"
    return {
        "route": route, "slot": selected,
        "as_of": parsed_date if scope is not None and not date_error else None,
        "intent": intent, "raw_intent": task["intent"], "task_code": task["code"],
        "discovery_status": discovery["status"], "discovery_reason": discovery["reason"],
        "candidates": [by_scope[(item["subject"], item["relation"])]
                       for item in discovery["candidates"]],
        "parsed_date": parsed_date, "date_error": date_error,
    }


def accuracy(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    correct = sum(row[field] for row in rows)
    return {"correct": correct, "total": len(rows),
            "accuracy": correct / len(rows) if rows else None}


def routing_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Precision and rejection denominators are explicit; clarification is not a hit."""
    def ratio(numerator, denominator):
        return {"count": numerator, "total": denominator,
                "rate": numerator / denominator if denominator else None}
    answerable = [r for r in rows if r["expected_slot"] is not None]
    automatic = [r for r in rows if r["actual"]["route"] in INTENTS and r["actual"]["slot"] is not None]
    correct = sum(r["route_slot_date_correct"] for r in automatic)
    return {
        "candidate_recall": ratio(sum(r["expected_slot"] in r["actual"].get("raw_candidates", []) for r in answerable), len(answerable)),
        "automatic_accuracy": ratio(correct, len(automatic)),
        "wrong_hit_rate": ratio(sum(not r["slot_correct"] for r in automatic), len(automatic)),
        "false_rejection_rate": ratio(sum(r["actual"]["route"] == "NO_MATCH" for r in answerable), len(answerable)),
        "clarify_rate": ratio(sum(r["actual"]["route"] == "CLARIFY" for r in rows), len(rows)),
        "automatic_coverage": ratio(len(automatic), len(rows)),
        "automatic_correct_share": ratio(correct, len(rows)),
    }


def grouped(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    return {value: {name: accuracy([row for row in rows if row[field] == value], name)
                    for name in ("route_correct", "slot_correct", "joint_correct")}
            for value in sorted({row[field] for row in rows})}


def confusion(rows: list[dict[str, Any]], expected: str, actual: str) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = {}
    for row in rows:
        gold, predicted = row[expected], row["actual"][actual]
        counts.setdefault(str(gold), Counter())[str(predicted)] += 1
    return {label: dict(counter) for label, counter in counts.items()}


def evaluate(cases_path: Path, legacy: tuple | None = None, *, retrieval: str = "bm25",
             live: LiveRoutingProvider | None = None) -> dict[str, Any]:
    cases = load_cases(cases_path)
    if live and legacy:
        raise ValueError("legacy evaluation cannot use a live provider")
    use_dense = retrieval == "hybrid" or (retrieval == "auto" and
        os.environ.get("LINKLOOM_RETRIEVAL_MODE") != "bm25")
    embedder = scope_discovery.get_slot_embedder() if use_dense and not legacy else None
    slots = [dict(slot, workspace_id=FIXTURE["workspace_id"], reviewed=True)
             for slot in deepcopy(FIXTURE["slots"])]
    original_slots = deepcopy(slots)
    resolver = FrozenRelationResolver(
        tuple(dict.fromkeys(slot["relation"] for slot in slots)),
        aliases=FIXTURE["relation_aliases"], schema_version="routing-fixture/v1")
    for module in (unified_ask, scope_discovery, question_routing):
        if not Path(module.__file__).resolve().is_relative_to(ROOT / "src"):
            raise RuntimeError(f"loaded routing code from another checkout: {module.__file__}")
    rows = []
    failed_case = None
    for case in cases:
        if live:
            live.case_id = case["id"]  # logging metadata only, never a model argument
        try:
            actual = (predict_legacy(case["query"], slots, resolver, legacy) if legacy else
                      predict(case["query"], slots, resolver, embedder=embedder, provider=live))
        except Exception as exc:
            if not live:
                raise
            live.stop("EVALUATION_ERROR:" + type(exc).__name__)
            failed_case = case["id"]
            break
        if live and live.failure:
            failed_case = case["id"]
            break  # no scoring a transport/budget failure as a model prediction
        row = dict(case, actual=actual)
        row["route_correct"] = actual["route"] == case["expected_route"]
        row["slot_correct"] = actual["slot"] == case["expected_slot"]
        row["intent_correct"] = actual["intent"] == case["expected_intent"]
        row["joint_correct"] = row["route_correct"] and row["slot_correct"]
        row["route_slot_date_correct"] = row["joint_correct"] and actual["as_of"] == case["expected_as_of"]
        rows.append(row)
        if live:
            with (live.run_dir / "case_results.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if llm_metrics([row])["hard_veto_overturned"]:
                live.stop("HARD_VETO_OVERTURNED")
                failed_case = case["id"]
                break
    if slots != original_slots:
        raise RuntimeError("routing mutated the fixed workspace projection")
    dated = [row for row in rows if row["expected_as_of"] is not None]
    date_correct = sum(row["actual"]["parsed_date"] == row["expected_as_of"] for row in dated)
    return {
        "schema_version": "routing-evaluation/v1",
        "implementation_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dataset_sha256": digest(cases_path.read_bytes()),
        "fixture_sha256": digest(json.dumps(FIXTURE, ensure_ascii=False, sort_keys=True).encode()),
        "evaluator_sha256": digest(Path(__file__).read_bytes()),
        "source_sha256": {str(Path(module.__file__).relative_to(ROOT)):
                          (module.__source_sha256__ if legacy else digest(Path(module.__file__).read_bytes()))
                          for module in (legacy if legacy else (unified_ask, scope_discovery, question_routing))},
        "retrieval": {"requested": retrieval, "actual": "hybrid" if embedder is not None else "bm25",
                      "model": getattr(embedder, "model_name", None),
                      "dense_recall_min_score": scope_discovery.SLOT_DENSE_RECALL_MIN_SCORE if embedder is not None else None},
        "boundary": "routing components on synthetic trusted slots; " + ("real selector provider; no UI/answer evaluation" if live else "offline; no provider/UI/answer evaluation"),
        "execution": {"mode": "live" if live else "offline", "completed": len(rows),
                      "requested": len(cases), "failed_case": failed_case,
                      "stop_reason": live.failure if live else None},
        "llm_metrics": llm_metrics(rows, live),
        "llm_usage": live.summary() if live else None,
        "fixture": FIXTURE,
        "metrics": {name: accuracy(rows, name) for name in (
            "joint_correct", "route_correct", "slot_correct", "intent_correct", "route_slot_date_correct")},
        "routing_metrics": routing_metrics(rows) if not legacy else None,
        "resolved_slot_accuracy": accuracy([row for row in rows if row["expected_slot"] is not None], "slot_correct"),
        "date_parser_accuracy": {"correct": date_correct, "total": len(dated),
                                 "accuracy": date_correct / len(dated) if dated else None},
        "by_route": grouped(rows, "expected_route"),
        "by_category": grouped(rows, "category"),
        "by_language": grouped(rows, "language"),
        "route_confusion": confusion(rows, "expected_route", "route"),
        "intent_confusion": confusion(rows, "expected_intent", "intent"),
        "slot_confusion": confusion(rows, "expected_slot", "slot"),
        "decided_by": ({layer: sum(row["actual"].get("decided_by") == layer for row in rows)
                        for layer in ("rule", "slot_match", "llm", "fallback")}
                       if not legacy else {"legacy": len(rows)}),
        "cases": rows,
    }


def score(metric: dict[str, Any]) -> str:
    percent = f"{metric['accuracy']:.1%}" if metric["accuracy"] is not None else "n/a"
    return f"{metric['correct']}/{metric['total']} ({percent})"


def print_matrix(matrix: dict[str, dict[str, int]], rows: list[str], columns: list[str]) -> None:
    print("| expected \\ actual | " + " | ".join(columns) + " |")
    print("|---|" + "---:|" * len(columns))
    for label in rows:
        print("| " + label + " | " + " | ".join(str(matrix.get(label, {}).get(column, 0))
                                               for column in columns) + " |")


def print_text(report: dict[str, Any], details: str) -> None:
    print(f"Revision: {report['implementation_revision']}")
    print(f"Dataset SHA256: {report['dataset_sha256']}")
    print(f"Fixture SHA256: {report['fixture_sha256']}")
    print(report["boundary"])
    print("Retrieval: " + json.dumps(report["retrieval"]))
    print("Execution: " + json.dumps(report["execution"]))
    print("LLM metrics: " + json.dumps(report["llm_metrics"]))
    if report["llm_usage"]:
        print("LLM usage/cost: " + json.dumps(report["llm_usage"], ensure_ascii=False))
    for slot in FIXTURE["slots"]:
        print(f"  {slot['slot_id']}: {slot['subject']} / {slot['relation']} / {' -> '.join(slot['values'])}")
    for name, metric in report["metrics"].items():
        print(f"{name}: {score(metric)}")
    if report.get("routing_metrics"):
        print("Routing metrics (explicit numerator/denominator): " + json.dumps(report["routing_metrics"]))
    print("resolved_slot_accuracy: " + score(report["resolved_slot_accuracy"]))
    print("date_parser_accuracy: " + score(report["date_parser_accuracy"]))
    for field in ("by_route", "by_category", "by_language"):
        print(f"\n{field} (joint = route AND slot)")
        print("| group | route | slot | joint |\n|---|---:|---:|---:|")
        for label, metrics in report[field].items():
            print(f"| {label} | " + " | ".join(score(metrics[key]) for key in
                  ("route_correct", "slot_correct", "joint_correct")) + " |")
    print("\nRoute confusion (rows=expected, columns=actual; UNKNOWN is preserved)")
    print_matrix(report["route_confusion"], list(ROUTES), [*ROUTES, "UNKNOWN"])
    print("\nIntent confusion (ignores scope/date admission failures)")
    print_matrix(report["intent_confusion"], list(INTENTS), [*INTENTS, "UNKNOWN"])
    print("\nSlot confusion (None means no selected slot)")
    slot_labels = [slot["slot_id"] for slot in FIXTURE["slots"]] + ["None"]
    print_matrix(report["slot_confusion"], slot_labels, slot_labels)
    if details != "none":
        print("\nCase details (expected -> actual)")
        for row in report["cases"]:
            if details == "failures" and row["route_slot_date_correct"] and row["intent_correct"]:
                continue
            actual = row["actual"]
            print(f"{row['id']} {row['query']}")
            print(f"  {row['expected_route']}/{row['expected_slot']} -> {actual['route']}/{actual['slot']}"
                  f"; raw={actual['raw_intent']}, scope={actual['discovery_status']}"
                  f"; as_of={row['expected_as_of']} -> {actual['as_of']}")
            print(f"  intent={row['expected_intent']} -> {actual['intent']}")
            print("  " + actual["discovery_reason"])
            if actual["date_error"]:
                print("  " + actual["date_error"]["code"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, metavar="JSONL",
                        help="UTF-8 JSONL cases for the fixed workspace; any nonempty case count. "
                             "Defaults to tests/routing/cases.jsonl.")
    parser.add_argument("--retrieval", choices=("auto", "bm25", "hybrid"), default="bm25",
                        help="bm25 is the provider-free product default; hybrid/auto explicitly compare the cached local model.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--compare", metavar="REVISION", help="Compare unchanged cases with routing at this Git revision")
    parser.add_argument("--details", choices=("none", "failures", "all"), default="none")
    parser.add_argument("--live-provider", choices=tuple(LIVE_PROVIDERS),
                        help="Explicit paid selector opt-in; default remains offline")
    parser.add_argument("--llm-run-dir", type=Path, help="Required new directory for durable live usage/results; never reused")
    parser.add_argument("--llm-max-requests", type=int, default=100, help="Hard cap 1..100; no automatic retries")
    parser.add_argument("--llm-max-cost-usd", type=Decimal, default=Decimal("1"), help="Hard cap >0 and <=1 USD")
    args = parser.parse_args()
    if bool(args.live_provider) != bool(args.llm_run_dir):
        parser.error("--live-provider and --llm-run-dir must be supplied together")
    if args.live_provider and args.compare:
        parser.error("--compare is offline-only; do not mix a real run with a replay")
    live = None
    if args.live_provider:
        try:
            load_cases(args.cases.resolve())  # validate all rows before spending money
            live = LiveRoutingProvider(args.live_provider, args.llm_run_dir,
                max_requests=args.llm_max_requests, max_cost_usd=args.llm_max_cost_usd)
        except (ImportError, ValueError, OSError, TypeError) as exc:
            parser.error(str(exc))
    report = evaluate(args.cases.resolve(), retrieval=args.retrieval, live=live)
    if live:
        with (live.run_dir / "report.json").open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    if args.compare:
        report["baseline"] = evaluate(args.cases.resolve(), load_legacy(args.compare), retrieval="bm25")
        report["baseline"]["implementation_revision"] = args.compare
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report, args.details)
        print("\nDecided by: " + json.dumps(report["decided_by"], sort_keys=True))
        if "baseline" in report:
            baseline = report["baseline"]
            print("\nOld/new exact route+slot accuracy")
            print("| category | old | new |\n|---|---:|---:|")
            print("| TOTAL | " + score(baseline["metrics"]["joint_correct"]) + " | "
                  + score(report["metrics"]["joint_correct"]) + " |")
            for category, metrics in report["by_category"].items():
                print("| " + category + " | " + score(baseline["by_category"][category]["joint_correct"])
                      + " | " + score(metrics["joint_correct"]) + " |")

    if live and live.failure:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
