"""Reproduce the frozen Canary with mocked SDK and an independent network deny.

No real Provider, countTokens or model probe is constructed. Evidence is wiring
and fault-injection evidence only; all semantic score dimensions remain pending.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import zipfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
import pytest
from scripts.evaluation_offline_plugin import no_external_provider_transport
from tests.m3l_support import offline_product, finish


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = dict(evidence_layer='OFFLINE_MOCKED_SDK_PRODUCT_INTEGRATION',
        actual_live_network_requests=0, actual_paid_requests=0, actual_paid_cost_usd=0,
        real_model_semantics='NOT_EVALUATED', real_business_completion='NOT_EVALUATED', cases={})
    # New short execution paths retain every artifact and avoid Windows MAX_PATH.
    execution = ROOT / 'work' / ('l-' + uuid4().hex[:6])
    with pytest.MonkeyPatch.context() as patch:
        no_external_provider_transport.__wrapped__(patch)
        p = offline_product(execution, patch)
        summary['freeze_sha256'] = p.spec.digest
        for case in p.spec.cases:
            p.sdk.calls.clear()
            p.sdk.receipts.clear()
            workspace = p.host._workspaces[case]
            p.app.repository.select_workspace(workspace)
            result, status = p.ask.ask(dict(query=p.spec.question, workspace_id=workspace))
            result = finish(p.ask, workspace, result)
            target = output / case
            target.mkdir()
            write(target / 'product-result.json', result)
            job = p.service._jobs.get(result['agent_run_id'])
            if job:
                audit = job.backend.engine.checkpoint_dir.parent
                with zipfile.ZipFile(target / 'runtime-audit.zip', 'w', zipfile.ZIP_DEFLATED) as z:
                    for file in sorted(audit.rglob('*')):
                        if file.is_file():
                            z.write(file, file.relative_to(audit).as_posix())
            scorecard = dict(evidence_layer='OFFLINE_MOCKED_SDK_ONLY', category=None,
                semantic_score='NOT_EVALUATED',
                dimensions={name: 'NOT_EVALUATED' for name in (
                    'continuity_coverage', 'precise_quote_dates', 'conflict',
                    'unconfirmed_assertions', 'excess_planning_missing_investigation')},
                mechanical_checks=dict(status=result['status'], reviewer=result['reviewer'],
                    formal_conclusions_published=bool(result['conclusions'])),
                explanation='Scripted outputs establish wiring only; refusal is not semantic success.')
            write(target / 'scorecard.json', scorecard)
            summary['cases'][case] = dict(status=result['status'], reviewer=result['reviewer'],
                mocked_sdk_sends=len(p.sdk.calls), gaps=result['preparation']['gaps'])
            if result['status'] != 'PARTIAL' or not result['reviewer'].get('accepted'):
                raise RuntimeError('Offline acceptance failed; artifacts retained: ' + str(target))
        write(output / 'budget-snapshot.json', p.host.ledger.snapshot())
        (output / 'provider-ledger.jsonl').write_bytes(p.host.ledger.ledger_path.read_bytes())
        p.app.close()
    write(output / 'summary.json', summary)
    print(json.dumps(dict(output=str(output), evidence_layer=summary['evidence_layer'],
        actual_live_network_requests=0, cases=summary['cases']), ensure_ascii=False))


if __name__ == '__main__':
    main()
