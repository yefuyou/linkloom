"""Create the pre-run freeze from a committed, clean implementation tree."""
from datetime import UTC, datetime
from pathlib import Path
import json
import platform
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from scripts.run_m2_final import digest, write


def main():
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('commit the implementation and acceptance tools before freezing')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    names=subprocess.check_output(['git','ls-files','src/linkloom','evals/m3_task_impact','scripts/run_m2_final.py',
        'scripts/score_m2_final.py','scripts/prepare_m2_holdout.py','scripts/freeze_m2_acceptance.py',
        'scripts/demonstrate_m2_integration.py','scripts/summarize_m2_dev.py','tests/fixtures/m2_final',
        'docs/requirements/m2-integration-acceptance/PROTOCOL.md',
        'docs/requirements/m2-integration-acceptance/evidence/holdout-snapshots.json'],cwd=ROOT,text=True).splitlines()
    gold='tests/fixtures/m2_final/gold/holdout.json'
    # The predictor can verify code and public data, but never opens any Gold.
    protected={n:digest(ROOT/n) for n in names if '/gold/' not in n and '/expected/' not in n}
    write(ROOT/'docs/requirements/m2-integration-acceptance/FREEZE.json',dict(
        schema='m2-acceptance-freeze/v1',created_at=datetime.now(UTC).isoformat(),
        implementation_commit=commit,baseline='eee882f4c676e900ee8895ac5b09f5fb2b3e6e36',
        included_commits=['98e6fde17d34886e1e35bb8eae77e1b0ef6a9422','526d59ef0f216940b2677402bb4ba5ff6d81cd32'],
        protocol='AUTHOR_VISIBLE_RETAINED_ACCEPTANCE; NO_EXTERNAL_BLIND_CLAIM; FIRST_EXECUTION_PENDING',
        hash_encoding='SHA256 of UTF-8 text with CRLF normalized to LF; source content hashes cover exact embedded content',
        rule_versions=['same-task-rules/v1','direct-relations-rules/v1 + target-fragment strip compatibility',
            'plan-revision-rules/v1','candidate-discovery/v1','task-relation-store/v1'],
        production_delta='Only task_work_proofs._target fragment.strip(); no heldout-driven semantic change',
        data_origin='M2_EVAL_SPEC.md H01-H04, fresh synthetic materialization, author-visible independent annotations',
        python=platform.python_version(),input_sha256=digest(ROOT/'tests/fixtures/m2_final/inputs/holdout.json'),
        evaluator_gold_sha256=digest(ROOT/gold),protected_hashes=protected))
    print(commit, len(protected),'protected files')


if __name__=='__main__':
    main()
