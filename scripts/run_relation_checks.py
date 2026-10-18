"""Offline checks with a fresh owned temp directory; never replay old ledgers."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
arguments = sys.argv[1:]
stage = 'p2'
if arguments[:1] == ['--stage']:
    stage = arguments[1]
    arguments = arguments[2:]
if stage not in {'p2', 'p3', 'p4'}:
    raise ValueError('unsupported relation milestone')
label = arguments[0]
if not label.replace('-', '').isalnum():
    raise ValueError('invalid evidence label')
folders = {'p2': 'm2-p2-task-relations', 'p3': 'm2-p3-task-revisions', 'p4': 'm2-p4-task-relations'}
out = root / 'work' / folders[stage] / 'acceptance-local'
out.mkdir(parents=True, exist_ok=True)
log = out / (label + '.txt')
if log.exists() or (out / ('tmp-' + label)).exists():
    raise FileExistsError('preserve previous evidence; choose a new label')
env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1',
           PYTHONIOENCODING='utf8', PYTHONPATH=str(root/'src') + os.pathsep + str(root),
           LINKLOOM_PYTEST_BASETEMP=str(out / ('tmp-' + label)))
cmd = [sys.executable, '-m', 'pytest', '-p', 'scripts.pytest_windows_tempdir', '-q',
       *arguments[1:], '--basetemp', str(out / ('tmp-' + label))]
result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding='utf8', errors='replace')
log.write_text(result.stdout + result.stderr, encoding='utf8')
sys.stdout.reconfigure(encoding='utf8', errors='replace')
print(result.stdout + result.stderr)
raise SystemExit(result.returncode)
