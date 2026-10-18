"""Offline checks with a fresh owned temp directory; never replay old ledgers."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
label = sys.argv[1]
if not label.replace('-', '').isalnum():
    raise ValueError('invalid evidence label')
out = root / 'work/m2-p2-task-relations/acceptance-local'
out.mkdir(parents=True, exist_ok=True)
log = out / (label + '.txt')
if log.exists() or (out / ('tmp-' + label)).exists():
    raise FileExistsError('preserve previous evidence; choose a new label')
env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1',
           PYTHONIOENCODING='utf8', PYTHONPATH=str(root/'src') + os.pathsep + str(root),
           LINKLOOM_PYTEST_BASETEMP=str(out / ('tmp-' + label)))
cmd = [sys.executable, '-m', 'pytest', '-p', 'scripts.pytest_windows_tempdir', '-q',
       *sys.argv[2:], '--basetemp', str(out / ('tmp-' + label))]
result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding='utf8', errors='replace')
log.write_text(result.stdout + result.stderr, encoding='utf8')
sys.stdout.reconfigure(encoding='utf8', errors='replace')
print(result.stdout + result.stderr)
raise SystemExit(result.returncode)
