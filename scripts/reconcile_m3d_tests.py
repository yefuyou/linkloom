"""Recheck only failed cases; count unique final outcomes, retaining all XML."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

DOC = Path('docs/requirements/m3-d-combined-acceptance')


def cases(path):
    for case in ET.parse(path).getroot().iter('testcase'):
        identity = case.attrib['classname'].replace('.', '/')+'.py::'+case.attrib['name']
        status = 'FAIL' if case.find('failure') is not None or case.find('error') is not None else 'SKIP' if case.find('skipped') is not None else 'PASS'
        yield identity, status


def main():
    initial = DOC/'evidence/regression.xml'
    recheck = DOC/'evidence/regression-short-path.xml'
    if sys.argv[1:] == ['recheck']:
        failed = [name for name,status in cases(initial) if status=='FAIL']
        temp = Path.cwd().parents[1]/'work/d-r1'
        assert not temp.exists(), 'USE_A_NEW_SHORT_TEMP_PATH; DO_NOT_DELETE_OLD_RECEIPTS'
        env = dict(os.environ, PYTHONPATH='src;.;tests/integration', PYTHONIOENCODING='utf8',
            PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', LINKLOOM_RETRIEVAL_MODE='bm25', GEMINI_API_KEY='', DEEPSEEK_API_KEY='')
        return subprocess.call([sys.executable,'-m','pytest','-q','--tb=line',*failed,
            '--basetemp='+str(temp),'--junitxml='+str(recheck)],env=env)
    final = dict(cases(initial))
    checks = dict(cases(recheck))
    assert set(checks)=={name for name,status in final.items() if status=='FAIL'}
    for name,status in checks.items(): final[name]=status
    report = dict(initial=dict(total=len(dict(cases(initial))), failures=sum(s=='FAIL' for _,s in cases(initial))),
        recheck=dict(total=len(checks),failures=sum(s=='FAIL' for s in checks.values())),
        unique_cases=len(final), final_pass=sum(s=='PASS' for s in final.values()),
        final_fail=sum(s=='FAIL' for s in final.values()), final_skip=sum(s=='SKIP' for s in final.values()),
        cases=final, batches_overlapping_not_added=True,
        initial_failure_cause='Windows MAX_PATH: long basetemp makes durable tool-result filenames exceed260; same code rechecked at short path.')
    (DOC/'evidence/regression-final.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print({k:v for k,v in report.items() if k!='cases'})
    return int(report['final_fail']>0)


if __name__=='__main__': raise SystemExit(main())
