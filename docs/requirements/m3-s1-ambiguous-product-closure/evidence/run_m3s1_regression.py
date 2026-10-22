import os,re,sys,subprocess
from pathlib import Path
s=Path('.github/workflows/offline-pr.yml').read_text(encoding='utf8')
block=s.split('name: Observation, planning, proof, context, Runtime and HTTP contracts',1)[1].split('- name: Explicit offline product entry',1)[0]
files=re.findall(r'tests/[^\s]+\.py',block)
files+=['tests/unit/test_task_field_binding.py','tests/unit/test_frozen_rehearsal_ledger.py','tests/unit/test_m3s1_planning_context.py','tests/integration/test_m3s1_ambiguous_product.py']
env=dict(os.environ,PYTHONPATH='src;.;tests/integration',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',LINKLOOM_RETRIEVAL_MODE='bm25',GEMINI_API_KEY='',DEEPSEEK_API_KEY='',PYTHONIOENCODING='utf-8')
Path('work/regression-selection.txt').write_text('\n'.join(files),encoding='utf8')
raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q',*files,'--basetemp=work/pytest-m3s1-regression','--junitxml=work/regression.xml'],env=env))
