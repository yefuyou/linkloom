"""Reproduce M3-I FakeModel product integration. No live-model semantic score."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
import zipfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from tests.m3i_support import product_case, finish, QUESTION
from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'work' / ('m3i-' + uuid4().hex[:8]))
    parser.add_argument('--serve-case', choices=('continuity', 'conflict', 'missing', 'replan'),
        help='Keep the selected synthetic case open in the existing Product UI/API after recording evidence.')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = dict(evidence_level='FAKE_MODEL_PRODUCT_INTEGRATION',
        real_model_semantic_test='NOT_EVALUATED', real_product_business_task='NOT_EVALUATED',
        new_paid_provider_requests=0, cases={})
    serve = None
    for name, options in (('continuity', {}), ('conflict', {'conflict': True}),
                          ('missing', {'missing': True}), ('replan', {'replan': True})):
        root = output / name
        root.mkdir()
        # Short operational paths avoid Windows' legacy path limit. The output
        # archive retains the complete artifacts independently of this location.
        execution_root = ROOT / 'work' / ('m3i-' + uuid4().hex[:8])
        execution_root.mkdir(parents=True)
        app, service, ask, stages, workspace = product_case(execution_root, **options)
        result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
        result = finish(ask, workspace, result)
        (root / 'product-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        # Include the exact Runtime checkpoint, requests, tool ledger, E mapping,
        # original-source recovery and Reviewer artifacts, without rewriting them.
        job = service._jobs.get(result['agent_run_id'])
        if job:
            audit_root = job.backend.engine.checkpoint_dir.parent
            with zipfile.ZipFile(root / 'runtime-audit.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(audit_root.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(audit_root).as_posix())
                archive.write(execution_root / 'product.sqlite', 'product.sqlite')
        # A portable synthetic product database lets the recorded run be
        # inspected alongside its source catalog. No personal configuration.
        if name == args.serve_case:
            serve = (app, service, ask, workspace, result)
        summary['cases'][name] = dict(status=result['status'], reviewer=result['reviewer'],
            stage_calls={stage.stage: len(stage.requests) for stage in stages},
            metrics=result['metrics'], gaps=result['preparation']['gaps'],
            source_count=len(result['preparation']['source_mapping']),
            trace='product-result.json', durable_audit='runtime-audit.zip')
        if name != args.serve_case:
            app.close()
        shutil.copyfile(execution_root / 'product.sqlite', root / 'product.sqlite')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'output': str(output), 'cases': {k: v['status'] for k, v in summary['cases'].items()}}, ensure_ascii=False))
    if any(v['status'] != ('UNKNOWN' if k == 'missing' else 'PARTIAL') or not v['reviewer'].get('accepted')
           for k, v in summary['cases'].items()):
        raise SystemExit('M3-I integration acceptance failed; inspect retained artifacts.')
    if serve:
        app, service, ask, workspace, result = serve
        # Reuse the exact normal Product server and Unified Ask instance. The
        # recorded run stays inspectable; no demo-only endpoint/backend exists.
        server = create_http_server(_UnusedRunBackend(), product_app=app,
                                    investigation_service=service, port=args.port)
        server.unified_ask = ask
        print(f'FAKE_MODEL_INTEGRATION — http://127.0.0.1:{server.server_port}', flush=True)
        print(f'Question: {QUESTION}', flush=True)
        print(f'Workspace: {workspace}; recorded run: {result["agent_run_id"]}', flush=True)
        print('New questions use scripted test adapters; this is NOT a real-model semantic evaluation.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.close()


if __name__ == '__main__':
    main()
