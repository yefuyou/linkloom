"""Record synthetic Product HTTP/Runtime evidence. No LIVE mode or Gold input."""
from pathlib import Path
import argparse
import json
import sys
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from tests.m3i_support import product_case
from tests.m3p_support import product_http, http_ask, revision_product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summary = dict(evidence_level='SCRIPTED_FAKE_PRODUCT_HTTP_RUNTIME',
        real_model_semantics='NOT_EVALUATED', provider_requests=0, count_tokens_requests=0,
        session_auto_recovery=False, cases={})
    for name in ('continuity', 'conflict', 'missing', 'replan', 'trusted', 'identity_only', 'no_records'):
        root = ROOT / 'work' / ('p-' + uuid4().hex[:6])
        root.mkdir()
        if name in {'trusted', 'identity_only', 'no_records'}:
            records = {'trusted': 'both', 'identity_only': 'identity', 'no_records': 'none'}[name]
            app, service, stages, workspace, *_ = revision_product(root, records=records)
        else:
            options = {} if name == 'continuity' else {name: True}
            app, service, _, stages, workspace = product_case(root, **options)
        try:
            with product_http(app, service) as base:
                result = http_ask(base, workspace)
            target = args.output / name
            target.mkdir()
            (target / 'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
            summary['cases'][name] = dict(status=result['status'],
                reviewer_accepted=result['reviewer'].get('accepted'),
                stage_calls={s.stage: len(s.requests) for s in stages},
                tools=result['task_review']['executed_tools'],
                steps=result['task_review']['steps'], source_count=len(result['preparation']['source_mapping']))
            job = service._jobs[result['agent_run_id']]
            with zipfile.ZipFile(target / 'runtime-audit.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
            assert result['status'] == ('UNKNOWN' if name == 'missing' else 'PARTIAL'), result
            assert result['reviewer']['accepted'], result
        finally:
            app.close()
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({name: data['status'] for name, data in summary['cases'].items()}))


if __name__ == '__main__':
    main()
