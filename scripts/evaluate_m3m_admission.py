"""New offline routing evidence only; never rewrites prior evaluations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
import pytest
from scripts.evaluation_offline_plugin import no_external_provider_transport
from tests.m3m_support import CASES_PATH, matrix, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execution-root', type=Path, required=True)
    args = parser.parse_args()
    with pytest.MonkeyPatch.context() as patch:
        no_external_provider_transport.__wrapped__(patch)
        rows = matrix(args.execution_root)
    data = dict(schema='m3m-route-evidence/v1',
        revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        implementation_sha256=hashlib.sha256((ROOT / 'src/linkloom/ui/unified_ask.py').read_bytes()).hexdigest(),
        fingerprints={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            'src/linkloom/ui/unified_ask.py', 'src/linkloom/ui/ask_admission.py',
            'src/linkloom/ui/scope_discovery.py', 'src/linkloom/ui/task_investigation.py',
            'src/linkloom/runtime/team_decision_planner.py', 'src/linkloom/runtime/adaptive_planning.py',
            'src/linkloom/ui/static/product.js', 'tests/m3m_support.py',
            'tests/fixtures/m3m_admission.json', 'scripts/evaluate_m3m_admission.py')},
        cases_sha256=hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        evidence_layer='OFFLINE_START_SPY_ROUTE_CONTRACT', real_model_semantics='NOT_EVALUATED',
        actual_live_requests=0, actual_paid_requests=0, counts=counts(rows), rows=rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    print(json.dumps(data['counts']))


if __name__ == '__main__':
    main()
