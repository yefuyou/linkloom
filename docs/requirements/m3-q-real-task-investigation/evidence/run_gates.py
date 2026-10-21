import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import yaml

root = Path.cwd()
workflow = yaml.safe_load((root / '.github/workflows/offline-pr.yml').read_text(encoding='utf8'))
selected = sys.argv[1:]
out = root / 'work/gates'
out.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONPATH='src;.;tests/integration', LINKLOOM_RETRIEVAL_MODE='bm25',
    PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8', GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
summary = []
for job in selected:
    for step in workflow['jobs'][job]['steps']:
        command = step.get('run', '')
        if 'python -m pytest' not in command:
            continue
        args = shlex.split(command)
        args[0] = sys.executable
        args = [a for a in args if not a.startswith('--junitxml=')]
        args += ['--junitxml=' + str(out / (job + '.xml')), '--basetemp=' + str(out / ('tmp-' + job)), '--tb=short']
        with (out / (job + '.log')).open('w', encoding='utf8') as stream:
            result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, env=env)
        summary.append(dict(job=job, returncode=result.returncode, command=args))
        print(job, result.returncode, flush=True)
(out / ('summary-' + '-'.join(selected) + '.json')).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
sys.exit(int(any(s['returncode'] for s in summary)))
