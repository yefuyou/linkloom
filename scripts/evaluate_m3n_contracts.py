"""Offline oracle-output contracts; NEVER estimates real model recall."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.m3n_contract_support import evaluate, load


def main():
    parser = ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("legacy", "cross-message-v1"), default="legacy")
    args = parser.parse_args()
    profile = None
    if args.profile != "legacy":
        from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
        profile = TaskDiscoveryProfile.CROSS_MESSAGE_V1
    data_root = ROOT / "docs/requirements/m3-n-task-discovery/data"
    report = dict(execution="OFFLINE_ORACLE_AND_ADVERSARIAL_SCRIPTED_OUTPUTS", profile=args.profile,
        real_model_semantic_quality="NOT_EVALUATED", real_provider_requests=0,
        interpretation="Gold supplies positive proposals; metrics measure Host contracts, not model inference.",
        datasets={})
    # Install the same external-transport denial used by all offline pytest runs.
    import pytest
    from scripts.evaluation_offline_plugin import no_external_provider_transport
    with pytest.MonkeyPatch.context() as monkeypatch:
        no_external_provider_transport.__wrapped__(monkeypatch)
        for name in ("dev", "heldout"):
            path = data_root / f"{name}.json"
            if path.exists():
                report["datasets"][name] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    **evaluate(load(path), task_profile=profile))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: v["totals"] for k,v in report["datasets"].items()}, indent=2))


if __name__ == "__main__":
    main()
