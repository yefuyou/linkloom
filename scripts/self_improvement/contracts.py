"""Frozen search space, trace attribution, and promotion gates (no model judge)."""
from collections import Counter
from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class Candidate:
    id: str
    change: str
    query_mode: str = "literal"
    read_mode: str = "all"
    limit: int = 4

    @classmethod
    def parse(cls, value):
        fields = {"id", "change", "query_mode", "read_mode", "limit"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Candidate fields must exactly match the whitelist")
        if not isinstance(value["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", value["id"]):
            raise ValueError("Invalid candidate id")
        if not isinstance(value["change"], str) or not 10 <= len(value["change"]) <= 1000:
            raise ValueError("Concrete change description required")
        if value["query_mode"] not in {"literal", "relation_terms"}:
            raise ValueError("Query mode outside whitelist")
        if value["read_mode"] not in {"all", "relation_candidates", "first_hit"}:
            raise ValueError("Read mode outside whitelist")
        if type(value["limit"]) is not int or value["limit"] not in {2, 4, 6}:
            raise ValueError("Search limit outside whitelist")
        return cls(**value)

    def to_dict(self):
        return asdict(self)


BASELINE = Candidate("baseline", "Unchanged production query strategy and offline controller")


def failure_signals(rows):
    """Gold-free symptoms with call sequence witnesses; never infer missing answers."""
    signals = []
    for row in rows:
        counts = Counter()
        seen_reads, seen_searches = set(), set()
        witnesses = []
        for call in row["calls"]:
            if call["tool"] == "read_verified_note" and call.get("evidence_id"):
                ref = call["evidence_id"]
                counts["duplicate_read"] += int(ref in seen_reads)
                seen_reads.add(ref)
            if call["tool"] == "search_notes":
                key = (call.get("hop"), call.get("variant", "primary"), call.get("query"))
                counts["duplicate_search"] += int(key in seen_searches)
                seen_searches.add(key)
                if call["status"] == "failed":
                    counts["tool_rejection"] += 1
                    counts["illegal_alternate"] += int(call.get("variant") == "alternate")
                    witnesses.append(call.get("sequence"))
                if call.get("returned_count") == 0:
                    counts["empty_search"] += 1
        if row.get("reason") == "evidence_insufficient":
            counts["unbound_next_hop"] += 1  # symptom, not proof of corpus recall failure
        if counts:
            signals.append({"case_id": row["id"], "counts": dict(counts),
                            "rejected_call_sequences": witnesses,
                            "outcome": row.get("outcome"), "reason": row.get("reason"),
                            "model_turns": row.get("model_turns"), "context_bytes": row.get("context_bytes"),
                            "citation_valid": row.get("citation_valid"),
                            "bindings_verified": row.get("bindings_verified"),
                            "claim_supported": row.get("claim_supported"),
                            "error_code": (row.get("error") or {}).get("code"),
                            "interpretation": "observed symptoms; recall loss needs separate scoring"})
    return signals


def promotion_gate(before, after):
    if after["safety_violations"]:
        return False, "safety_veto"
    if after.get("runtime_failures", 0) > before.get("runtime_failures", 0):
        return False, "runtime_regression"
    for field in ("accuracy", "grounding", "refusal_accuracy"):
        if after[field] < before[field]:
            return False, field + "_regression"
    if after["calls"] > before["calls"] * 1.10 or after["context_bytes"] > before["context_bytes"] * 1.10:
        return False, "cost_regression"
    for field in ("model_calls", "context_total_bytes"):
        if field in before and after[field] > before[field] * 1.10:
            return False, field + "_regression"
    improved = (after["accuracy"] > before["accuracy"] or
                after["calls"] <= before["calls"] * .90 or
                after["context_bytes"] <= before["context_bytes"] * .90)
    return improved, "improved" if improved else "no_material_gain"


def targeted_reductions(before_signals, after_rows, candidate):
    """Require measured reduction of a symptom addressed by the changed knobs."""
    targeted = set()
    if candidate.query_mode != "literal":
        targeted.update({"unbound_next_hop", "empty_search"})
    if candidate.read_mode != "all" or candidate.limit != 4:
        targeted.update({"duplicate_read", "duplicate_search"})
    after = {row["case_id"]: row["counts"] for row in failure_signals(after_rows)}
    return [{"case_id": row["case_id"], "signal": signal, "before": count,
             "after": after.get(row["case_id"], {}).get(signal, 0)}
            for row in before_signals for signal, count in row["counts"].items()
            if signal in targeted and count > after.get(row["case_id"], {}).get(signal, 0)]
