"""Prepare an experiment packet. This module has no generation execution path."""
import argparse
import hashlib
import json
import pathlib
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

from .diagnostics import proxy_snapshot, versions


def prepare(output, experiment_id):
    from .ledger import DiagnosticLedger
    # Exclusive creation prevents identity reuse or historical overwrite.
    output.mkdir(parents=True, exist_ok=False)
    ledger = DiagnosticLedger(output/'experiment.ledger.jsonl', experiment_id=experiment_id)
    root = pathlib.Path(__file__).resolve().parents[2]
    files = ['scripts/gemini_transport/diagnostics.py', 'scripts/gemini_transport/ledger.py',
             'scripts/gemini_transport/product.py', 'tests/smoke/test_p14_cn2_typed_gemini38_smoke.py',
             'scripts/gemini_transport/sdk.py', 'src/linkloom/agents/providers/gemini_api.py',
             'tests/smoke/run_temporal_product_http.py']
    packet = {
        'experiment_id': ledger.experiment_id, 'prepared_utc': datetime.now(timezone.utc).isoformat(),
        'state': 'PREPARED_NOT_AUTHORIZED', 'generation_requests_executed': 0,
        'model': 'gemini-3.8-flash', 'max_generation_requests': 4, 'sdk_attempts': 1,
        'timeout_ms': 130000, 'concurrency': 1, 'experiment_wall_seconds': 600,
        'max_output_tokens_per_request': 1024, 'estimated_input_token_cap': 12000,
        'stop_on': ['UNCERTAIN', 'ledger_error', 'deadline', 'budget_exhausted'],
        'planned_requests': [
            {'identity': ledger.experiment_id+'-01', 'client': 'A', 'input': 'synthetic_short'},
            {'identity': ledger.experiment_id+'-02', 'client': 'A', 'input': 'synthetic_short'},
            {'identity': ledger.experiment_id+'-03', 'client': 'B', 'input': 'synthetic_short'},
            {'identity': ledger.experiment_id+'-04', 'client': 'B', 'input': 'synthetic_long_new'},
        ],
        'python': sys.version, 'tls': ssl.OPENSSL_VERSION, 'versions': versions(),
        'environment_proxies': {scheme: proxy_snapshot(value) for scheme, value in urllib.request.getproxies().items() if scheme in ('http','https','all')},
        'tun_route': 'UNKNOWN_REQUIRE_EXECUTION_SNAPSHOT',
        'source_sha256': {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files},
        'limits': 'Four calls cannot identify all network, SDK, length and reuse effects; no statistical stability claim.',
    }
    (output/'protocol.json').write_text(json.dumps(packet, indent=2), encoding='utf-8')
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    parser.add_argument('--experiment-id', required=True)
    args = parser.parse_args()
    packet = prepare(args.output, args.experiment_id)
    print(json.dumps({'state': packet['state'], 'generation_requests_executed': 0}))


if __name__ == '__main__':
    main()
