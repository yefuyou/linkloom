"""Prepare/execute one independently authorized synthetic Unified Ask experiment.

Default is prepare only. --demo uses injected offline HTTPX transport exclusively.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from .diagnostics import versions

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ['scripts/gemini_transport/diagnostics.py','scripts/gemini_transport/product.py',
           'scripts/gemini_transport/unified.py','src/linkloom/agents/providers/gemini_api.py',
           'src/linkloom/ui/unified_ask.py','src/linkloom/ui/investigation.py','src/linkloom/ui/backend.py',
           'src/linkloom/ui/server.py','tests/smoke/test_p14_cn2_typed_gemini38_smoke.py',
           'tests/integration/unified_transport_fixture.py','tests/integration/unified_ask_fixture.py']
QUESTION = 'Why did Launch change from Birchline to Wrenwell? Which formal supplier is current, when did it change, and what original sources support this?'


def fingerprints():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def protocol_for(identity):
    return {'schema_version':'unified-ask-transport/v1','experiment_id':identity,
        'state':'PREPARED_NOT_AUTHORIZED','real_generation_requests':0,
        'endpoint':'/api/product/ask','model':'gemini-3.8-flash','query':QUESTION,
        'subject':'Launch','relation':'uses supplier','as_of':'2026-10-10',
        'max_runtime_runs':1,'max_generation_requests':6,'concurrency':1,
        'sdk_attempts':1,'request_timeout_ms':130000,'new_send_budget_seconds':900,
        'inflight_grace_seconds':130,'terminal_grace_seconds':5,
        'diagnostics':True,'proxy_mode':'CURRENT_ENVIRONMENT_UNCHANGED',
        'stop_on':['UNCERTAIN','budget_exhausted','deadline','startup_evidence_unavailable'],
        'authorization_env':'LINKLOOM_UNIFIED_TRANSPORT_AUTHORIZATION',
        'required_authorization':'AUTHORIZE '+identity,
        'source_sha256':fingerprints(),'versions':versions(),
        'output_directory':'executed',
        'input_output_parameters':'Product defaults unchanged; tokens/cost observed, not dollar hard capped.',
        'success_dimensions':['transport_evidence','runtime_complete','validator_reviewer','gold']}


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    protocol = protocol_for('UAT-' + uuid4().hex)
    with (directory/'protocol.json').open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(protocol,indent=2))
    return protocol


def validate_authorization(directory):
    protocol = json.loads((Path(directory)/'protocol.json').read_text(encoding='utf-8'))
    identity = protocol.get('experiment_id','')
    import re
    if not re.fullmatch(r'UAT-[0-9a-f]{32}',identity):
        raise ValueError('EXPERIMENT_ID_INVALID')
    expected = protocol_for(identity)
    if protocol.get('source_sha256')!=expected['source_sha256'] or protocol.get('versions')!=expected['versions']:
        raise ValueError('EXPERIMENT_FINGERPRINT_CHANGED')
    # Exact canonical comparison also locks audit metadata, unknown fields and JSON types.
    if json.dumps(protocol,sort_keys=True)!=json.dumps(expected,sort_keys=True):
        raise ValueError('EXPERIMENT_BOUNDARY_CHANGED')
    if os.environ.get('LINKLOOM_UNIFIED_TRANSPORT_AUTHORIZATION')!='AUTHORIZE '+identity:
        raise PermissionError('INDEPENDENT_AUTHORIZATION_REQUIRED')
    return protocol


def remaining_terminal_seconds(protocol, send_deadline):
    remaining = send_deadline + protocol['inflight_grace_seconds'] + protocol['terminal_grace_seconds'] - monotonic()
    if remaining<=0:
        raise TimeoutError('EXPERIMENT_TERMINAL_UNCONFIRMED_STOP')
    return remaining


def execute(directory):
    directory = Path(directory)
    protocol = validate_authorization(directory)
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        raise PermissionError('GEMINI_CREDENTIAL_UNAVAILABLE')
    # Exclusive run reservation disallows restarting or replaying this identity.
    executed = directory/'executed'
    executed.mkdir(exist_ok=False)
    with (executed/'experiment_reservation.json').open('x',encoding='utf-8') as stream:
        stream.write(json.dumps({'experiment_id':protocol['experiment_id'],'max_runtime_runs':1,
                                 'unclosed_reservation_means':'UNCERTAIN_STOP_NO_REPLAY'}))
    from tests.integration.unified_transport_fixture import UnifiedTransportHarness
    from tests.integration.unified_ask_fixture import request_json, ATLAS
    os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
    harness = UnifiedTransportHarness(executed,diagnostics=True,live_key=key,max_runtime_runs=1,
        new_send_budget_seconds=protocol['new_send_budget_seconds'])
    try:
        status,payload = request_json(harness.fixture.base_url,'/api/product/ask',method='POST',timeout=160,data={
            'query':QUESTION,'workspace_id':harness.fixture.workspace_ids[ATLAS],
            'subject':'Launch','relation':'uses supplier','as_of':'2026-10-10','allow_external_provider':True})
        if status!=202:
            raise RuntimeError('UNIFIED_START_REJECTED')
        run_id = payload['result']['agent_run_id']
        run = harness.runs[0]
        while payload['result']['status']=='INVESTIGATING':
            sleep(min(.25,remaining_terminal_seconds(protocol,run['send_deadline'])))
            status,payload = request_json(harness.fixture.base_url,
                f'/api/product/ask/runs/{run_id}?workspace_id={harness.fixture.workspace_ids[ATLAS]}',
                timeout=min(30,remaining_terminal_seconds(protocol,run['send_deadline'])))
        result = payload['result']
        run['ledger'].finish_run(status=result['status'],usage_summary={})
        measured = {'experiment_id':protocol['experiment_id'],'product_run_id':run_id,
            'provider_requests':run['ledger'].request_count,'diagnostic_failures':run['diagnostics'].failures,
            'transport_evidence':run['diagnostics'].failures==0 and any(e['event'].startswith('http11.') for e in run['events']),
            'runtime_complete':result['status']=='COMPLETED',
            'validator_reviewer':result['reviewer']['accepted'],
            'gold':result['status']=='COMPLETED' and result['conclusions'][0]['value']=='Wrenwell'
                   and 'replacement_reason' in result['uncertainty']['unknown_fields'],
            'status':result['status']}
        with (executed/'acceptance.json').open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(measured,indent=2))
        print(json.dumps(measured))
        return measured
    finally:
        harness.close()


def demo(directory):
    from tests.integration.unified_transport_fixture import UnifiedTransportHarness
    os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
    root = Path(directory)/('demo-'+uuid4().hex)
    harness = UnifiedTransportHarness(root,diagnostics=True,max_runtime_runs=10,delay=.2)
    print(json.dumps({'mode':'OFFLINE_ONLY','url':harness.fixture.base_url,'provider_requests_real':0}),flush=True)
    try:
        while harness.fixture.worker.is_alive():
            harness.fixture.worker.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        harness.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true')
    mode.add_argument('--demo',action='store_true')
    args = parser.parse_args(argv)
    if args.execute:
        execute(args.directory)
    elif args.demo:
        demo(args.directory)
    else:
        packet = prepare(args.directory)
        print(json.dumps({'experiment_id':packet['experiment_id'],'state':packet['state'],'real_generation_requests':0}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        # Do not print traceback / SDK exception messages, which can contain credentials.
        print(json.dumps({'state':'STOPPED','error_class':'PermissionError' if isinstance(exc,PermissionError) else 'ERROR'}))
        raise SystemExit(2)
