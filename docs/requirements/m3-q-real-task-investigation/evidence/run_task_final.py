"""Run the Task workflow selector on the final source with a short synthetic temp root."""
from pathlib import Path
import os
import shlex
import subprocess
import sys
import yaml

root = Path.cwd()
workflow = yaml.safe_load((root / '.github/workflows/offline-pr.yml').read_text(encoding='utf8'))
command = next(step['run'] for step in workflow['jobs']['task-investigation']['steps']
               if 'python -m pytest' in step.get('run', ''))
args = shlex.split(command)
args[0] = sys.executable
args = [arg for arg in args if not arg.startswith('--junitxml=')]
args += ['--basetemp=C:/Users/18019/AppData/Local/Temp/m3qtf4',
         '--junitxml=work/gates/task-investigation-final.xml', '--tb=short']
env = dict(os.environ, PYTHONPATH='src;.;tests/integration', LINKLOOM_RETRIEVAL_MODE='bm25',
           PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
           GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
with (root / 'work/gates/task-investigation-final.log').open('w', encoding='utf8') as stream:
    result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, env=env)
print((root / 'work/gates/task-investigation-final.log').read_text(encoding='utf8'))
sys.exit(result.returncode)
