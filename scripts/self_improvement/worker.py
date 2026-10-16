"""Fresh-process execution. This module accepts public cases, never Gold."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]

from scripts.self_improvement.contracts import Candidate
from scripts.self_improvement.policy import ExperimentalQueryStrategy, controller_type


def execute(public, candidate, root):
    import run_bounded_multihop as runner
    from linkloom.agents import runtime_adapter
    from linkloom.agents.claim_grounding import assess_claim_text, ClaimSupportStatus

    actions, verified, searches, context_sizes = [], {}, [], []
    policy = controller_type(runner.SourceOnlyController, candidate)
    original_query = runtime_adapter.RuntimeAgentAdapter._planned_search_query

    class ObservedController(policy):
        def __call__(self, request):
            context_sizes.append(len(json.dumps(request.to_dict(), ensure_ascii=False).encode("utf-8")))
            observation = request.observation
            if observation and observation.status == "ok":
                if observation.tool_id == "search_notes" and actions:
                    actions[-1]["returned_count"] = len(observation.value)
                if observation.tool_id == "read_verified_note":
                    verified[observation.value["evidence_id"]] = observation.value
            action = super().__call__(request)
            if action.kind == "tool_call":
                args = action.tool_call.arguments
                actions.append({"query": args.get("query"),
                                "variant": args.get("source_context", {}).get("query_variant", "primary")})
            return action

    def observed_query(adapter, context):
        query = original_query(adapter, context)
        searches.append(query)
        return query

    with ExitStack() as stack:
        stack.enter_context(patch.object(runner, "SourceOnlyController", ObservedController))
        stack.enter_context(patch.object(runtime_adapter, "SubgoalQueryStrategy",
                                         lambda: ExperimentalQueryStrategy(candidate.query_mode)))
        stack.enter_context(patch.object(runtime_adapter.RuntimeAgentAdapter, "_planned_search_query", observed_query))
        result = runner.run_case(public, root)
    for call, action in zip(result["calls"], actions):
        call.update(action)
    result["compiled_queries"] = searches
    result["context_total_bytes"] = sum(context_sizes)
    result["tool_attempts"] = len(actions)
    final = result.get("final")
    texts = [] if not final else ([final["decision"]["value"]] if final["decision"]["value"] else
                                  [item["point"] for item in final["rationale"]])
    result["claim_supported"] = all(assess_claim_text(text, final["evidence_refs"], verified).status
                                    == ClaimSupportStatus.SUPPORTED for text in texts)
    result["verified_read_dependency"] = result["bindings_verified"]
    allowed = {doc["path"] for doc in public["documents"] if doc["workspace_id"] == public["workspace_id"]}
    result["workspace_valid"] = all(call.get("source", "") in allowed for call in result["calls"]
                                     if call["tool"] == "read_verified_note" and call["status"] == "completed")
    result["safety_violations"] = max(0, len(actions) - len(result["calls"])) + sum(
        call["status"] == "failed" for call in result["calls"]) + sum(
        not result[key] for key in ("citation_valid", "claim_supported", "bindings_verified", "workspace_valid"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = Candidate.parse(json.loads(args.candidate.read_text(encoding="utf-8")))
    cases = json.loads(args.public.read_text(encoding="utf-8"))["cases"]
    rows = []
    for case in cases:
        if set(case) != {"id", "query", "workspace_id", "documents", "as_of"}:
            raise ValueError("Public case fields invalid: oracle or unsupported input")
        # Durable Runtime filenames are long. Keep execution paths shallow on
        # Windows; the report still records each unique artifact instance.
        runtime_root = ROOT / "work/si" / uuid4().hex[:10]
        result = execute({k: v for k, v in case.items() if k != "id"}, candidate, runtime_root)
        rows.append({"id": case["id"], "artifact_root": str(runtime_root), **result})
    args.output.write_text(json.dumps({"candidate": candidate.to_dict(), "cases": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
