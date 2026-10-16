"""Export admission-route evidence from saved HTTP responses; no provider calls."""
from collections import Counter
import json
from pathlib import Path

from scripts.adaptive_live import ROOT, OUT, write_new


def main():
    public = {}
    for path in (ROOT / "tests/fixtures/adaptive_planning").glob("public_*.json"):
        public.update({x["id"]: x for x in json.loads(path.read_text(encoding="utf8"))["cases"]})
    cases = []
    for path in sorted((OUT / "runs").glob("*/*-raw.json")):
        raw = json.loads(path.read_text(encoding="utf8"))
        if raw["variant"] != "B":
            continue
        response = raw.get("unified_result") or {}
        route = response.get("route") or raw.get("route") or {}
        expected = public[raw["case_id"]]["expected_route"]
        # Routing is distinct from plan validity, execution and correct refusal.
        # Rejected inputs may enter source admission but must stop before tools.
        correct = (route.get("intent") == "SOURCE_RELATION" if expected == "relation_multihop"
                   else raw["status"] == "NEEDS_SCOPE" and not raw.get("tool_trace"))
        cases.append({"case_id": raw["case_id"], "expected_route": expected,
            "actual_route": route, "status": raw["status"], "route_correct": correct,
            "proposal_events": raw.get("observed_plans", []),
            "error": response.get("error"), "raw_relative_path": path.relative_to(ROOT).as_posix()})
    write_new(ROOT / "docs/evaluation/adaptive_planning/routing.json", {
        "schema_version": "adaptive-route-audit/v1", "cases": cases,
        "summary": dict(Counter("correct" if x["route_correct"] else "incorrect" for x in cases)),
        "missing": sorted(set(public) - {x["case_id"] for x in cases}),
        "criterion": "Supported input reaches SOURCE_RELATION; reject input stops NEEDS_SCOPE before tools. Not execution success."})


if __name__ == "__main__":
    main()
