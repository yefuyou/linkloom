#!/usr/bin/env python3
"""Offline product HTTP/SQLite demonstration; requires .[dev], no Provider.

Uses existing public synthetic fixtures, original P4 proof construction and
the normal Runtime/Reviewer. Only fixture setup writes relation sidecars.
Rejects existing output directories to preserve every prior receipt.
"""
import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT), str(ROOT / 'tests/integration')]

from tests.integration.test_product_application_flow import _client
from tests.integration.test_task_relation_triage import EditedOfflineStage, checks
from tests.m3p_support import cold_read, http_ask, product_case, product_http, revision_product


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    output = Path(parser.parse_args().output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    summaries = []
    for name in ('possible', 'unknown', 'conflict', 'p4', 'reverse', 'missing', 'retracted'):
        directory = output / name
        directory.mkdir()
        if name in {'p4', 'reverse', 'retracted'}:
            app, service, stages, ws, identity, *_ = revision_product(directory)
            if name == 'retracted':
                from tests.unit.test_task_relation_query import retract
                retract(directory / 'relations.sqlite', ws, identity.assessment_id)
            if name == 'reverse':
                def reverse(arguments):
                    next(s for s in arguments['steps'] if s['id'] == 'revision')['handles'].reverse()
                service.task_models = replace(service.task_models,
                    planner=EditedOfflineStage(stages[1], reverse))
        else:
            app, service, _, stages, ws = product_case(directory,
                conflict=name == 'conflict', missing=name == 'missing')
            if name == 'conflict':
                def conflict(arguments):
                    for p in arguments['proposals']:
                        p['proposed_verdict'] = 'CONFLICT'
                        p['counter_evidence'] = p['supporting_evidence'][-1:]
                        p['supporting_evidence'] = p['supporting_evidence'][:2]
                service.task_models = replace(service.task_models,
                    semantics=EditedOfflineStage(stages[2], conflict))
            elif name == 'unknown':
                def unknown(arguments):
                    for p in arguments['proposals']:
                        p['proposed_verdict'] = 'UNKNOWN'
                        p['missing_evidence'] = ['No independent identity proof']
                service.task_models = replace(service.task_models,
                    semantics=EditedOfflineStage(stages[2], unknown))
        sidecar = directory / 'relations.sqlite'
        before = sidecar.read_bytes() if sidecar.exists() else None
        try:
            write(directory / 'sources.json', app.snapshot_investigation_sources(ws)[1])
            with product_http(app, service) as base:
                result = http_ask(base, ws)
                write(directory / 'result.json', result)
                if name == 'possible':
                    row, = checks(result, 'RELATED_TO')
                    assert row['state'] == 'POSSIBLE' and row['candidates'] and not row['steps']
                elif name in {'unknown', 'conflict'}:
                    assert checks(result, 'SAME_TASK')[0]['state'] == 'NOT_ESTABLISHED'
                elif name == 'p4':
                    assert all(checks(result, p)[0]['state'] == 'ESTABLISHED'
                        for p in ('SAME_TASK', 'REVISES_PLAN'))
                elif name == 'reverse':
                    assert not any(r['state'] == 'ESTABLISHED' for r in checks(result, 'REVISES_PLAN'))
                elif name == 'missing':
                    assert result['status'] == 'UNKNOWN' and not result['task_review']['relation_triage']['checks']
                else:
                    assert result['task_review']['relation_triage']['p4_history_included'] is False
                    assert all(row['state'] != 'ESTABLISHED'
                        for row in result['task_review']['relation_triage']['checks'])
                calls = [len(stage.requests) for stage in stages]
                code, saved = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
                assert code == 200 and saved['result']['task_review'] == result['task_review']
                assert [len(stage.requests) for stage in stages] == calls
            code, saved = cold_read(directory / 'product.sqlite', result['agent_run_id'], ws,
                sidecar if sidecar.exists() else None)
            assert code == 200 and saved['result']['task_review'] == result['task_review']
            write(directory / 'cold.json', saved['result'])
            assert (sidecar.read_bytes() if sidecar.exists() else None) == before
            if name == 'possible':
                with product_http(app, service) as base:
                    other = app.repository.create_workspace('Other synthetic workspace')['workspace_id']
                    code, blocked = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={other}")
                    write(directory / 'cross-workspace.json', dict(http_status=code, response=blocked))
                    assert code in {404, 409} and not blocked.get('result', {}).get('task_review'), (code, blocked)
                    app.repository.select_workspace(ws)
                    with app.repository._lock, app.repository._connection:
                        app.repository._connection.execute(
                            'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                            (ws, result['evidence'][0]['evidence_ref']))
                    code, revoked = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
                    assert code == 200 and revoked['result']['status'] == 'BLOCKED'
                    assert not revoked['result'].get('task_review') and not revoked['result']['evidence']
                    write(directory / 'revoked-hot.json', revoked['result'])
                code, revoked = cold_read(directory / 'product.sqlite', result['agent_run_id'], ws)
                assert code == 200 and revoked['result']['status'] == 'BLOCKED'
                assert not revoked['result'].get('task_review') and not revoked['result']['evidence']
                write(directory / 'revoked-cold.json', revoked['result'])
            summaries.append(dict(case=name, status=result['status'],
                states=[dict(predicate=r['predicate'], state=r['state'], directed=r['directed'])
                    for r in result['task_review']['relation_triage']['checks']],
                p4_history_included=result['task_review']['relation_triage']['p4_history_included'],
                stage_calls=calls, p4_bytes_unchanged=True, new_live_provider_calls=0,
                p4_sha256=sha256(before).hexdigest() if before else None))
        finally:
            app.close()
    write(output / 'summary.json', dict(mode='SYNTHETIC_SCRIPTED_PRODUCT_HTTP', cases=summaries,
        historical_m2='UNCHANGED_0_OF_4_FAIL', real_model_semantics='NOT_EVALUATED'))
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == '__main__':
    main()
