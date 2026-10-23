"""Verify and replay saved receipts using only Python's standard library."""
from pathlib import Path,PurePosixPath
from zipfile import ZipFile
import argparse,hashlib,json,os,subprocess,sys

def read(p):return json.loads(p.read_text(encoding='utf8'))
def sha(data):return hashlib.sha256(data).hexdigest()

def main(output):
    here=Path(__file__).resolve().parent;manifest=read(here/'EVIDENCE_MANIFEST.json')
    archive=here/'evidence.zip'
    assert sha(archive.read_bytes())==manifest['archive_sha256'],'ARCHIVE_HASH_MISMATCH'
    assert sha((here/'REPORT.md').read_bytes())==manifest['report_sha256'],'REPORT_HASH_MISMATCH'
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    with ZipFile(archive) as z:
        assert len(z.namelist())==len(set(z.namelist()))
        assert set(z.namelist())==set(manifest['files'])
        for entry in z.infolist():
            path=PurePosixPath(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='work'
            dest=(output/entry.filename).resolve();assert dest.is_relative_to(output)
            data=z.read(entry);expected=manifest['files'][entry.filename]
            assert len(data)==expected['bytes'] and sha(data)==expected['sha256'],entry.filename
            # Runtime receipt locators can exceed legacy Windows MAX_PATH.
            native=Path('\\\\?\\'+str(dest)) if os.name=='nt' else dest
            native.parent.mkdir(parents=True,exist_ok=True);native.write_bytes(data)
    root=output/'work/cloud-credit-sprint-20261023'
    for script,target in [('analyze_batch_a.py','verified-a'),('analyze_continuation.py','verified-b-e')]:
        result=subprocess.run([sys.executable,str(root/script),'--output',str(root/target)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode:
            sys.stderr.buffer.write(result.stderr);raise RuntimeError('OFFLINE_REPLAY_FAILED:'+script)
    original=read(root/'continuation-analysis-v2/aggregate.json')
    replay=read(root/'verified-b-e/aggregate.json')
    assert original['total']==replay['total'],'TOTAL_REPLAY_MISMATCH'
    assert read(root/'analysis-final/aggregate.json')==read(root/'verified-a/aggregate.json')
    assert replay['synthetic_admitted']==7 and replay['C_proposals']==20
    assert replay['C_quote_validation_failures']==1 and replay['D_equal']==4
    print(json.dumps(dict(status='PASS',files=len(manifest['files']),provider_calls=0,
        generated_requests=replay['total']['actual_wire_requests'],responses=replay['total']['responses'],
        reserved_usd=replay['total']['reserved_usd'],estimated_token_cost_usd=replay['total']['token_price_estimate_usd']),indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();main(args.output)
