"""OFFLINE only: normal Product Host with real Adapter and scripted transport.

No API key, external HTTP or frozen answer files are used. This is wiring and
trust acceptance, never a measurement of model understanding.
"""
from argparse import ArgumentParser
from pathlib import Path
import json
import subprocess
import sys

from tests.integration.test_m3q_provider_host import empty_provider_product, ask_request, poll
from tests.m3p_support import product_http, TEXTS
from tests.integration.test_product_application_flow import _client


def write(root, name, data):
    (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def main():
    parser = ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--serve', action='store_true', help='Keep the OFFLINE Product UI open for browser acceptance.')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    app, service, transport = empty_provider_product(root)
    try:
        with product_http(app, service) as base:
            write(root, 'mode.json', dict(mode='OFFLINE_SCRIPTED_TRANSPORT',
                real_provider='REAL_PROVIDER_NOT_EVALUATED', real_provider_requests=0, actual_cost_usd=0,
                model_configured='gemini-3.8-flash', base_url=base))
            code, body = _client(base, '/api/workspaces', method='POST', data={'name': 'OFFLINE · M3-Q public meetings'})
            assert code == 201, body
            ws = body['workspace']['workspace_id']
            _client(base, '/api/workspaces/current', method='POST', data={'workspace_id': ws})
            for i, text in enumerate(TEXTS[:2]):
                code, body = _client(base, '/api/sources/import', method='POST',
                    data=dict(name=f'public-meeting-{i + 1}.txt', content=text))
                assert code == 202, body
            assert not transport.requests
            write(root, 'config.json', _client(base, '/api/product/ask/config')[1])
            code, body = ask_request(base, ws)
            assert code == 403 and not transport.requests, body
            write(root, 'permission-denied.json', body)
            if args.serve:
                transport.latency_seconds = .15
                print(base + '/', flush=True)
                print('OFFLINE scripted transport. REAL_PROVIDER_NOT_EVALUATED.', flush=True)
                input('Press Enter after browser acceptance to finish.\n')
            code, body = ask_request(base, ws, authorized=True)
            assert code == 202, body
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL' and result['reviewer']['accepted'], result
            write(root, 'final.json', result)
            count = len(transport.requests)
            child = subprocess.Popen([sys.executable, '-u', '-m', 'linkloom.ui', '--database',
                str(root / 'product.sqlite'), '--port', '0'], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf8')
            try:
                line = child.stdout.readline().strip()
                assert line.startswith('LinkLoom Product UI: http://127.0.0.1:'), line
                cold_base = line.split(': ', 1)[1]
                status, config = _client(cold_base, '/api/product/ask/config')
                assert status == 200 and not config['agent']['available'] and not config['agent']['external_provider']
                write(root, 'ordinary-cli-config.json', config)
                status, cold = _client(cold_base,
                    f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
                assert status == 200 and cold['result']['status'] == 'PARTIAL', cold
                assert not cold['result']['recovery']['session_resumed']
            finally:
                child.terminate()
                child.communicate(timeout=15)
            assert len(transport.requests) == count
            write(root, 'cold.json', cold)
            write(root, 'summary.json', dict(mode='OFFLINE', status='PASS',
                provider_adapter_requests=count, real_provider_requests=0, actual_cost_usd=0,
                real_provider='REAL_PROVIDER_NOT_EVALUATED', workspace_id=ws, agent_run_id=result['agent_run_id']))
    finally:
        app.close()


if __name__ == '__main__':
    main()
