"""Optional final Gemini compatibility/strategy audit; cannot change a finalist.

This auxiliary opinion is never a scorer or a promotion input. It receives
only the selected configuration and DEV symptom projection, with no answers.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from scripts.self_improvement.contracts import Candidate
from scripts.self_improvement.provider import generate, accounting
from scripts.self_improvement.run import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to((ROOT / "work/self-improvement-lab").resolve()):
        raise ValueError("Not a local lab experiment")
    candidate = Candidate.parse(json.loads((run / "winner/final/candidate.json").read_text(encoding="utf-8")))
    feedback = json.loads((run / "round-1/feedback.json").read_text(encoding="utf-8"))
    provider_root = ROOT / "work/self-improvement-lab"
    if generate('Return only {"compatible": true}.', provider_root) != {"compatible": True}:
        raise RuntimeError("Compatibility check failed")
    audit = generate("Audit a frozen search strategy. Do not propose or modify candidates. "
        "Return JSON with candidate_id and concerns (a list of short strings). This is advisory; "
        "you cannot grade answers or approve promotion. No gold or heldout data is available. "
        "Query relation_terms rewrites only verified dependent queries to entity+relation words. "
        "read_mode relation_candidates reads the first hit plus keyword-relevant hits, always "
        "through existing verified reads. Existing permissions, binding and grounding are fixed. "
        "Data:\n" + json.dumps({"candidate": candidate.to_dict(), "dev_feedback": feedback}), provider_root)
    if not isinstance(audit, dict) or audit.get("candidate_id") != candidate.id:
        raise ValueError("Invalid advisory audit")
    write(run / "live_verification.json", {"compatible": True, "advisory_only": True,
          "selection_influence": False, "audit": audit, "provider": accounting(provider_root)})
    print(json.dumps({"compatible": True, "candidate_id": candidate.id,
                      "provider": accounting(provider_root)}))


if __name__ == "__main__":
    main()
