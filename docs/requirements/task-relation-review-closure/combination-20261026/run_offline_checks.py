"""Run the merged workflow's offline commands, preserving every raw log."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import yaml

root = Path(sys.argv[1])
out = root / 'work/task-relation-review/final-combination-20261026/checks'
out.mkdir(parents=True, exist_ok=False)
env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root/'src'),str(root),str(root/'tests/integration')]), PYTHONUTF8='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', LINKLOOM_RETRIEVAL_MODE='bm25')
for key in ['OPENAI_API_KEY','OPENAI_BASE_URL','GOOGLE_API_KEY','GEMINI_API_KEY','DEEPSEEK_API_KEY']:
    env[key] = ''
workflow = yaml.safe_load((root/'.github/workflows/offline-pr.yml').read_text())
results = []
for job, definition in workflow['jobs'].items():
    for step in definition['steps']:
        command = step.get('run','')
        if not (command.startswith('python -m pytest') or command.startswith('node --check') or command.startswith('python scripts/run_m3p_offline_acceptance.py')):
            continue
        number = len(results)
        name = step.get('name', job)
        args = shlex.split(command)
        if command.startswith('node --check'):
            commands = [shlex.split(line) for line in command.strip().splitlines()]
        else:
            args[0] = sys.executable
            args = [f'--junitxml={out/(str(number)+"-results.xml")}' if a.startswith('--junitxml=') else a for a in args]
            if '-m' in args and 'pytest' in args:
                args += ['--basetemp', tempfile.mkdtemp(prefix=f'll26-{number}-')]
            if '--output' in args:
                args[args.index('--output')+1] = str(out/'offline-product')
            commands = [args]
        print(f'START {number}: {name}',flush=True)
        started = time.time()
        codes=[]
        with (out/f'{number}-raw.log').open('w',encoding='utf8') as log:
            for args in commands:
                log.write(json.dumps(args)+'\n'); log.flush()
                codes.append(subprocess.run(args,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT).returncode)
        row=dict(number=number,job=job,name=name,commands=commands,codes=codes,seconds=round(time.time()-started,2))
        results.append(row)
        (out/'commands.json').write_text(json.dumps(results,indent=2),encoding='utf8')
        print(f'END {number}: {codes}, {row["seconds"]}s',flush=True)
cases={}
for xml in out.glob('*-results.xml'):
    for case in ET.parse(xml).iter('testcase'):
        identity=(case.get('classname'),case.get('name'))
        cases[identity]='FAIL' if case.find('failure') is not None or case.find('error') is not None else 'SKIP' if case.find('skipped') is not None else 'PASS'
summary=dict(commands=results,unique_cases=len(cases),pass_count=list(cases.values()).count('PASS'),fail_count=list(cases.values()).count('FAIL'),skip_count=list(cases.values()).count('SKIP'))
(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in summary.items() if k!='commands'}),flush=True)
sys.exit(1 if any(any(c!=0 for c in r['codes']) for r in results) else 0)
