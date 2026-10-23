"""Run launch checks and bind their evidence to the tested frozen source."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from linkloom.evaluation.canary_trust import save_new, sha
from scripts.run_ami_canary_v2 import ROOT, code_identity, head, runtime_identity

SUITES = {
    'quote': ['tests/unit/test_canary_quote_scopes.py', 'tests/unit/test_quote_nesting_p2.py',
              'tests/unit/test_text_scopes.py', 'tests/unit/test_p2_work_identity.py'],
    'runner': ['tests/unit/test_canary_trust_v2.py', 'tests/unit/test_ami_canary_v2.py', 'tests/unit/test_sprint_trust_v2.py'],
    'safety': ['tests/unit/test_task_observation_discovery.py', 'tests/unit/test_task_recall_protocol.py',
               'tests/unit/test_task_field_binding.py', 'tests/unit/test_task_offline_permissions.py',
               'tests/unit/test_task_investigation_p4_steps.py', 'tests/unit/test_task_result_projection.py',
               'tests/unit/test_final_trust_boundary.py', 'tests/unit/test_source_identity_contract.py',
               'tests/unit/test_m3q_provider_budget.py'],
}


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    frozen_sha, hashes = head(), code_identity()
    for name, tests in SUITES.items():
        report = output / f'{name}.xml'
        env = dict(os.environ, PYTHONPATH=f'{ROOT / "src"}{os.pathsep}{ROOT}',
            PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
            GEMINI_API_KEY='', GOOGLE_API_KEY='', OPENAI_API_KEY='', DEEPSEEK_API_KEY='',
            LINKLOOM_PYTEST_BASETEMP=str((output / f'tmp-{name}').resolve()))
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'scripts.pytest_windows_tempdir',
                   f'--basetemp={output / ("tmp-" + name)}', *tests, f'--junitxml={report}']
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        save_new(output / f'{name}.log.json', dict(command=command, exit_code=result.returncode,
                                                  stdout=result.stdout, stderr=result.stderr))
        if result.returncode or code_identity() != hashes or head() != frozen_sha:
            raise ValueError(f'{name} checks failed or tested source changed')
        save_new(report.with_suffix('.identity.json'), dict(frozen_sha=frozen_sha, code_hashes=hashes,
            runtime=runtime_identity(), report_sha256=sha(report.read_bytes()), suite=name, tests=tests))
        print(f'{name}: PASS', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
