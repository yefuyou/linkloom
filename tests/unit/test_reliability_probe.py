"""Offline contract checks for the authorized experiment's send admission."""
import pytest

from scripts.reliability_probe import SendBudget, MODEL, REQUEST_RESERVATION_USD


def payload(**extra):
    return {'model': MODEL, 'contents': [{'parts': [{'text': 'Synthetic only'}]}], 'config': {}, **extra}


def test_send_budget_has_conservative_cost_and_exact_request_cap():
    budget = SendBudget()
    for _ in range(60):
        budget.admit(payload())
        budget.busy = False
    assert budget.attempts == 60
    assert budget.reserved_usd == pytest.approx(60*REQUEST_RESERVATION_USD)
    assert budget.reserved_usd < 10
    with pytest.raises(RuntimeError, match='SPRINT_SEND_LIMIT'):
        budget.admit(payload())
    assert budget.attempts == 60


def test_admission_rejects_concurrency_expired_window_media_and_oversized_text():
    budget = SendBudget()
    for invalid in (payload(model='gemini-2.5-flash'), payload(contents='x'*65536),
                    payload(contents=[{'inline_data': {'data': 'synthetic'}}])):
        with pytest.raises(RuntimeError):
            budget.admit(invalid)
        assert budget.attempts == 0
    budget.admit(payload())
    with pytest.raises(RuntimeError, match='SPRINT_SEND_LIMIT'):
        budget.admit(payload())
    budget.busy = False
    budget.started -= 5400
    with pytest.raises(RuntimeError, match='SPRINT_SEND_LIMIT'):
        budget.admit(payload())


def test_admission_stops_before_dollar_limit():
    budget = SendBudget()
    budget.reserved_usd = 10-REQUEST_RESERVATION_USD/2
    with pytest.raises(RuntimeError, match='SPRINT_COST_LIMIT'):
        budget.admit(payload())
    assert budget.attempts == 0


def test_live_lease_blocks_another_process_and_releases_on_close(tmp_path):
    import subprocess
    import sys
    from scripts.reliability_probe import live_execution_lock, ROOT
    child = ('from pathlib import Path\nfrom scripts.reliability_probe import live_execution_lock\n'
             'import sys\ntry:\n with live_execution_lock(Path(sys.argv[1])): pass\n'
             'except OSError: sys.exit(7)\n')
    def attempt():
        return subprocess.run([sys.executable, '-c', child, str(tmp_path)], cwd=ROOT,
            capture_output=True, timeout=15).returncode
    with live_execution_lock(tmp_path):
        assert attempt() == 7
    assert attempt() == 0


def test_live_dataset_allowlist_blocks_unapproved_text_before_sdk(tmp_path):
    import json
    from scripts.reliability_probe import execute
    public = tmp_path/'public.json'
    public.write_text(json.dumps({'cases': [{'query': 'private synthetic sentinel'}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='UNAPPROVED_LIVE_DATASET'):
        execute.__wrapped__(live=True, dataset=public)


def test_live_rag_cannot_move_its_ledger_outside_canonical_budget_root(tmp_path):
    from scripts.reliability_rag_baseline import execute
    with pytest.raises(ValueError, match='LIVE_OUTPUT_ROOT_MUST_BE_CANONICAL'):
        execute.__wrapped__(live=True, attempt='synthetic', output_root=tmp_path)
