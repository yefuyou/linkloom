"""Three stages over the existing A/B executor and budget, never a second runner."""
from contextlib import closing, contextmanager
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from linkloom.evaluation import task_profile_ab as ab
from linkloom.agents.model_adapter import ModelResponse, ModelTurnRequest


STAGES = ('FIRST_PAIR', 'VERIFY_FIRST_PAIR', 'CONTINUE_REMAINING')
SUFFIXES = ('request', 'response', 'sdk', 'host')


@contextmanager
def campaign_lock(root):
    """Nonblocking kernel lock, released on process exit; never delete lock files."""
    root.mkdir(parents=True, exist_ok=True)
    with (root/'campaign.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('CAMPAIGN_ALREADY_RUNNING') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _audit(db, kind, detail):
    db.execute('INSERT INTO events(kind,detail,at) VALUES (?,?,?)',
        (kind,json.dumps(detail,sort_keys=True),datetime.now(UTC).isoformat()))


def _transition(db, state, gate=None):
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('UPDATE campaign SET state=?,gate=COALESCE(?,gate)',
            (state,json.dumps(gate,sort_keys=True) if gate is not None else None))
        _audit(db,state,gate or {})
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def _bundle(output, identity):
    return {suffix:hashlib.sha256((output/(identity+'.'+suffix+'.json')).read_bytes()).hexdigest()
            for suffix in SUFFIXES}


def _read_bundle(output, identity, expected):
    data = {}
    for suffix in SUFFIXES:
        raw = (output/(identity+'.'+suffix+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected[suffix]:
            raise ValueError('RESULT_EVIDENCE_MISSING_OR_CHANGED')
        data[suffix] = json.loads(raw.decode('utf8'))
    return data


def _schema_valid(response):
    # Execution validity only. No labels, rubric or semantic quality enter this gate.
    from linkloom.semantic_ingestion.extraction import _extract_tool_arguments, _parse_claim
    try:
        payload = _extract_tool_arguments(response)
        if not isinstance(payload,dict) or set(payload) != {'claims'} or not isinstance(payload['claims'],list) or len(payload['claims'])>20:
            return False
        for claim in payload['claims']:
            _parse_claim(claim)
        return True
    except (ValueError,TypeError,KeyError,AttributeError):
        return False


def _usage_valid(record, freeze):
    usage = record.get('usage')
    fields = ('prompt_token_count','candidates_token_count','thoughts_token_count','total_token_count')
    if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in fields):
        return False
    p,c,t,total = (usage[k] for k in fields)
    config = freeze['config']
    return (p<=config['input_token_cap'] and c+t<=config['max_output_tokens']
        and total==p+c+t and record.get('wire_sends')==1
        and record.get('actual_model_version')==config['model_id']
        and record.get('reservation_usd')==config['reservation_usd'])


def _host_contract(value):
    value = json.loads(json.dumps(value))
    # Replay has a different local wall-clock duration, never different evidence.
    receipts = value['receipts'] + [row['receipt'] for row in value['extractions']]
    for receipt in receipts:
        if len(receipt['attempts'])!=1:
            raise ValueError('EXTRACTION_ATTEMPT_COUNT_CHANGED')
        duration = receipt['attempts'][0].pop('latency_ms')
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:
            raise ValueError('INVALID_EXTRACTION_LATENCY')
    return value


def _validate_prefix(budget, output, freeze, grant, count):
    """Read-only replay from durable ledger, including exact Host reconstruction."""
    ab.validate_grant(grant,freeze)
    if ab.load_freeze()['manifest_sha256']!=freeze['manifest_sha256']:
        raise ValueError('LIVE_FREEZE_CHANGED')
    if ab.read(output/'freeze.json')!=freeze:
        raise ValueError('OUTPUT_FREEZE_MISMATCH')
    identities = [(s,arm,profile,s['id']+'-'+arm) for s in freeze['sources']
                  for arm,profile in freeze['config']['arms'].items()][:count]
    rows = budget.db.execute('SELECT identity,request,status,response FROM requests ORDER BY rowid').fetchall()
    if len(rows)!=count or [r[0] for r in rows]!=[r[3] for r in identities] or any(r[2]!='COMPLETE' for r in rows):
        raise ValueError('LEDGER_PREFIX_NOT_COMPLETE')
    if count*freeze['config']['reservation_usd']>freeze['config']['max_cost_usd']:
        raise ValueError('BUDGET_EXCEEDED')
    pairs, entries, requests = [], [], {}
    from google.genai import types
    from linkloom.agents.providers import GeminiProviderAdapter
    for (source,arm,profile,identity),(_,request_json,_,record_json) in zip(identities,rows,strict=True):
        sealed = budget.db.execute('SELECT hashes FROM artifacts WHERE identity=?',(identity,)).fetchone()
        if sealed is None:
            raise ValueError('RESULT_EVIDENCE_MISSING_OR_CHANGED')
        values = _read_bundle(output,identity,json.loads(sealed[0]))
        request,record,response_json = (values[k] for k in ('request','sdk','response'))
        if request!=json.loads(request_json) or record!=json.loads(record_json) or response_json!=record['normalized']:
            raise ValueError('LEDGER_RESULT_MISMATCH')
        response = ModelResponse.from_dict(response_json)
        if response.error or not _schema_valid(response) or not _usage_valid(record,freeze):
            raise ValueError('RESULT_SCHEMA_USAGE_OR_VERSION_INVALID')
        # Parse the actual raw SDK record again, never invent a replacement response.
        raw = types.GenerateContentResponse.model_validate(record['sdk_response'])
        replay = GeminiProviderAdapter(SimpleNamespace(generate_content=lambda **_:raw),
            model_id=freeze['config']['model_id']).complete(ModelTurnRequest.from_dict(request)).to_dict()
        replay['usage']['duration_ms'] = response_json['usage']['duration_ms']
        if replay!=response_json or record['sdk_response'].get('usage_metadata')!=record['usage']:
            raise ValueError('RAW_SDK_RESPONSE_MISMATCH')
        def replay_request(current):
            if current.to_dict()!=request:
                raise ValueError('FROZEN_REQUEST_CHANGED')
            return response
        host = ab.discover(freeze,source,profile,SimpleNamespace(complete=replay_request)).to_dict()
        if _host_contract(host)!=_host_contract(values['host']):
            raise ValueError('HOST_EVIDENCE_MISMATCH')
        entries.append(dict(identity=identity,hashes=json.loads(sealed[0])))
        requests[arm] = request
        if arm=='B':
            a,b = requests['A'],requests['B']
            pair = dict(case=source['id'],schema_equal=a['available_tools']==b['available_tools'],
                options_equal=a['generation_options']==b['generation_options'],profile_different=ab.digest(a)!=ab.digest(b),
                request_sha256={k:ab.digest(v) for k,v in requests.items()})
            if not all(pair[k] for k in ('schema_equal','options_equal','profile_different')):
                raise ValueError('PAIRING_INVALID')
            pairs.append(pair)
    return dict(manifest_sha256=freeze['manifest_sha256'],entries=entries,pairs=pairs)


def _send_pair(budget, live, output, freeze, grant, source):
    for arm,profile in freeze['config']['arms'].items():
        identity = source['id']+'-'+arm
        def send(request):
            ab.save(output/(identity+'.request.json'),request.to_dict())
            response,record = live.complete(request,identity)
            ab.save(output/(identity+'.sdk.json'),record)
            ab.save(output/(identity+'.response.json'),response.to_dict())
            return response
        result = ab.discover(freeze,source,profile,SimpleNamespace(complete=send))
        ab.save(output/(identity+'.host.json'),result.to_dict())
        # Discovery catches Provider errors; the durable ledger is authoritative.
        row = budget.db.execute('SELECT status FROM requests WHERE identity=?',(identity,)).fetchone()
        if row!=('COMPLETE',):
            raise ValueError('REQUEST_NOT_COMPLETE_STOP')
        record = ab.read(output/(identity+'.sdk.json'))
        if not _schema_valid(ModelResponse.from_dict(record['normalized'])) or not _usage_valid(record,freeze):
            raise ValueError('RESULT_SCHEMA_USAGE_OR_VERSION_INVALID')
        budget.db.execute('INSERT INTO artifacts VALUES (?,?)',
            (identity,json.dumps(_bundle(output,identity),sort_keys=True)))
        _audit(budget.db,'ARTIFACTS_SEALED',dict(identity=identity))


def _check_existing_ledger(path, freeze):
    """Never initialize or repair an existing empty/damaged campaign database."""
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
                raise ValueError('CAMPAIGN_LEDGER_CORRUPT')
            if db.execute('SELECT manifest FROM binding').fetchall()!=[(freeze['manifest_sha256'],)]:
                raise ValueError('CAMPAIGN_LEDGER_BINDING_MISMATCH')
            for table,columns in (('requests','identity,request,status,response'),
                ('campaign','output,state,gate'),('artifacts','identity,hashes'),('events','seq,kind,detail,at')):
                db.execute('SELECT '+columns+' FROM '+table+' LIMIT 0')
    except sqlite3.Error:
        raise ValueError('CAMPAIGN_LEDGER_CORRUPT') from None


def _publish_final(db, output, report):
    """Publish a complete report without overwriting any existing artifact."""
    target = output/'execution.json'
    if target.exists():
        if ab.read(target)!=report:
            raise ValueError('FINAL_REPORT_MISMATCH')
        return
    _audit(db,'FINAL_REPORT_PREPARE',dict(sha256=ab.digest(report)))
    seq = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    pending = output/f'execution-{seq}.pending.json'
    ab.save(pending,report)
    # Windows rename is atomic and refuses an existing target. POSIX rename
    # overwrites, so use its no-overwrite hard-link primitive instead.
    if os.name == 'nt':
        os.rename(pending,target)
    else:
        os.link(pending,target)


def execute_stage(output, freeze, grant, stage):
    if stage not in STAGES or tuple(freeze['control']['stages'])!=STAGES:
        raise ValueError('EXPLICIT_STAGE_REQUIRED')
    output = Path(output).resolve()
    root = ab.live_root()
    if stage!='FIRST_PAIR' and not (root/'budget.sqlite3').is_file():
        raise ValueError('CAMPAIGN_NOT_INITIALIZED')
    with campaign_lock(root):
        path = root/'budget.sqlite3'
        if stage=='FIRST_PAIR' and path.exists():
            raise ValueError('FIRST_PAIR_ALREADY_STARTED_OR_LEDGER_EXISTS')
        if stage!='FIRST_PAIR':
            _check_existing_ledger(path,freeze)
        budget = ab.Budget(root/'budget.sqlite3',freeze)
        db = budget.db
        live = None
        started = False
        sends = 0
        try:
            if stage=='FIRST_PAIR':
                db.execute('CREATE TABLE campaign (output TEXT PRIMARY KEY,state TEXT,gate TEXT)')
                db.execute('CREATE TABLE artifacts (identity TEXT PRIMARY KEY,hashes TEXT)')
                db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY,kind TEXT,detail TEXT,at TEXT)')
            current = db.execute('SELECT output,state,gate FROM campaign').fetchall()
            if stage=='FIRST_PAIR':
                if current or db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] or output.exists():
                    raise ValueError('FIRST_PAIR_ALREADY_STARTED_OR_OUTPUT_EXISTS')
                output.mkdir(parents=True)
                ab.save(output/'freeze.json',freeze)
                db.execute('INSERT INTO campaign VALUES (?,?,NULL)',(str(output),'FIRST_RUNNING'))
                _audit(db,'FIRST_RUNNING',{})
                started = True
                live = ab.open_live(freeze,grant,budget=budget)
                _send_pair(budget,live,output,freeze,grant,freeze['sources'][0])
                sends = 2
                checked = _validate_prefix(budget,output,freeze,grant,2)
                _transition(db,'FIRST_DONE')
                status,gate = 'FIRST_PAIR_PAUSED','NOT_VERIFIED'
            else:
                if len(current)!=1 or current[0][0]!=str(output):
                    raise ValueError('CAMPAIGN_OUTPUT_MISMATCH')
                _,state,stored_gate = current[0]
                if stage=='VERIFY_FIRST_PAIR':
                    if state not in ('FIRST_RUNNING','FIRST_FAILED','FIRST_DONE','VERIFIED'):
                        raise ValueError('FIRST_PAIR_NOT_VERIFIABLE')
                    started = True
                    checked = _validate_prefix(budget,output,freeze,grant,2)
                    _transition(db,'VERIFIED',dict(FIRST_PAIR_GATE='PASS',evidence=checked))
                    status,gate = 'FIRST_PAIR_VERIFIED','PASS'
                else:
                    if state not in ('VERIFIED','CONTINUE_RUNNING','FINALIZING','COMPLETED') or stored_gate is None:
                        raise ValueError('FIRST_PAIR_GATE_REQUIRED_OR_CONTINUATION_INTERRUPTED')
                    # An interrupted sending phase can ONLY validate all 26 and
                    # finalize read-only; a shorter/unknown prefix cannot resume.
                    finalizing = state!='VERIFIED'
                    checked = _validate_prefix(budget,output,freeze,grant,26 if finalizing else 2)
                    first = dict(checked,entries=checked['entries'][:2],pairs=checked['pairs'][:1])
                    if json.loads(stored_gate)!=dict(FIRST_PAIR_GATE='PASS',evidence=first):
                        raise ValueError('FIRST_PAIR_GATE_MISMATCH')
                    if not finalizing:
                        if (output/'execution.json').exists():
                            raise ValueError('FINAL_REPORT_ALREADY_EXISTS')
                        _transition(db,'CONTINUE_RUNNING')
                        started = True
                        live = ab.open_live(freeze,grant,budget=budget)
                        for index,source in enumerate(freeze['sources'][1:],2):
                            _send_pair(budget,live,output,freeze,grant,source)
                            checked = _validate_prefix(budget,output,freeze,grant,index*2)
                            _audit(db,'PAIR_VERIFIED',dict(count=index*2,evidence=ab.digest(checked)))
                        sends = 24
                        _transition(db,'FINALIZING')
                    status,gate = 'LIVE_AB_COMPLETED','PASS'
            count = len(checked['entries'])
            report = dict(mode='live',stage=stage,status=status,FIRST_PAIR_GATE=gate,
                manifest_sha256=freeze['manifest_sha256'],counts={'A':count//2,'B':count//2},
                real_provider_requests=count,REAL_MODEL_SEMANTIC_EFFECT='NOT_EVALUATED',
                rows=[dict(identity=e['identity'],case=e['identity'][:-2],arm=e['identity'][-1]) for e in checked['entries']],
                pairs=checked['pairs'],reserved_usd=count*freeze['config']['reservation_usd'])
            if status=='LIVE_AB_COMPLETED':
                _publish_final(db,output,report)
                if db.execute('SELECT state FROM campaign').fetchone()[0]!='COMPLETED':
                    _transition(db,'COMPLETED')
            return dict(report,sends_this_invocation=sends)
        except BaseException as exc:
            if started and db.execute('SELECT state FROM campaign').fetchone()[0]!='FINALIZING':
                _transition(db,{'FIRST_PAIR':'FIRST_FAILED','VERIFY_FIRST_PAIR':'VERIFY_FAILED',
                    'CONTINUE_REMAINING':'CONTINUE_FAILED'}[stage],
                    dict(FIRST_PAIR_GATE='FAIL') if stage=='VERIFY_FIRST_PAIR' else None)
            _audit(db,'STAGE_REJECTED',dict(stage=stage,reason=type(exc).__name__,detail=str(exc)))
            raise
        finally:
            if live is not None:
                live.sdk.close()
            budget.close()
