import json
import subprocess
import pytest
from pathlib import Path
import hashlib
import re

def run_cli(*args):
    cmd = ["python", "-m", "linkloom", "memory", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result


def run_linkloom(*args):
    return subprocess.run(
        ["python", "-m", "linkloom", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

def test_memory_cli_lifecycle(tmp_path):
    store_path = tmp_path / "memory.jsonl"
    
    # 1. Propose candidate
    res_propose = run_cli(
        "propose",
        "--store", str(store_path),
        "--scope", "global",
        "--key", "user_preference",
        "--value-json", '{"theme": "dark"}',
        "--source-ref", "note1.md"
    )
    assert res_propose.returncode == 0
    assert "user_preference" in res_propose.stdout
    assert "pending" in res_propose.stdout
    assert "theme" not in res_propose.stdout  # value not exposed
    
    # Read the candidate list
    res_candidates = run_cli("candidates", "--store", str(store_path))
    assert res_candidates.returncode == 0
    assert "user_preference" in res_candidates.stdout
    
    with open(store_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    candidate_id = events[0]["payload"]["id"]
    
    # 2. Confirm candidate
    res_confirm = run_cli(
        "confirm",
        "--store", str(store_path),
        "--candidate-id", candidate_id,
        "--actor", "user",
        "--edited-json", '{"theme": "light"}'
    )
    assert res_confirm.returncode == 0
    assert "active" in res_confirm.stdout
    assert candidate_id in res_confirm.stdout
    assert "light" not in res_confirm.stdout  # value not exposed
    
    # Extract item ID
    with open(store_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    item_id = events[1]["payload"]["item_id"]
    
    # 3. List active
    res_list = run_cli("list", "--store", str(store_path))
    assert res_list.returncode == 0
    assert "user_preference" in res_list.stdout
    assert "active" in res_list.stdout
    assert "theme" not in res_list.stdout
    
    # 4. Revoke
    res_revoke = run_cli(
        "revoke",
        "--store", str(store_path),
        "--item-id", item_id,
        "--actor", "user"
    )
    assert res_revoke.returncode == 0
    assert "revoked" in res_revoke.stdout
    
    # List active again, should not contain the item
    res_list2 = run_cli("list", "--store", str(store_path))
    assert res_list2.returncode == 0
    assert item_id not in res_list2.stdout

def test_memory_cli_policy_violation(tmp_path):
    store_path = tmp_path / "memory.jsonl"
    
    res = run_cli(
        "propose",
        "--store", str(store_path),
        "--scope", "global",
        "--key", "secret_key",
        "--value-json", '{"secret": "password"}',
        "--source-ref", "note1.md"
    )
    assert res.returncode != 0
    assert "POLICY" in res.stderr.upper() or "ERROR" in res.stderr.upper()
    assert "password" not in res.stderr

def test_memory_cli_malformed_json(tmp_path):
    store_path = tmp_path / "memory.jsonl"
    
    res = run_cli(
        "propose",
        "--store", str(store_path),
        "--scope", "global",
        "--key", "test",
        "--value-json", '{bad json}',
        "--source-ref", "note1.md"
    )
    assert res.returncode != 0
    assert "JSON" in res.stderr.upper() or "ERROR" in res.stderr.upper()


def test_agent_status_result_and_trace_do_not_expose_memory_value(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    vault = repo / "tests" / "fixtures" / "sample_vault"
    before = {
        path.relative_to(vault): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in vault.rglob("*")
        if path.is_file()
    }
    memory_root = tmp_path / "memory"
    store = memory_root / "memory.jsonl"
    sentinel = "memory-sentinel-DO-NOT-LEAK"

    proposed = run_linkloom(
        "memory", "propose", "--store", str(store), "--scope", "global",
        "--key", "agent_preference", "--value-json", json.dumps({"summary": sentinel}),
        "--source-ref", "manual",
    )
    assert proposed.returncode == 0, proposed.stderr
    candidate_id = json.loads(store.read_text(encoding="utf-8").splitlines()[0])["payload"]["id"]
    confirmed = run_linkloom(
        "memory", "confirm", "--store", str(store), "--candidate-id", candidate_id, "--actor", "human",
    )
    assert confirmed.returncode == 0, confirmed.stderr

    scan_dir = tmp_path / "scan"
    scanned = run_linkloom("scan", str(vault), "--output", str(scan_dir))
    assert scanned.returncode == 0, scanned.stderr
    checkpoint = tmp_path / "checkpoint"
    traces = tmp_path / "traces"
    run = run_linkloom(
        "agent", "ask", str(vault), "--index", str(scan_dir / "vault_index.json"),
        "--query", "Scanner", "--checkpoint", str(checkpoint), "--trace", str(traces),
        "--memory-root", str(memory_root),
    )
    assert run.returncode == 0, run.stderr
    assert sentinel not in run.stdout

    result_match = re.search(r"Result: (.+)", run.stdout)
    assert result_match
    result_path = Path(result_match.group(1).strip())
    assert sentinel not in result_path.read_text(encoding="utf-8")
    for path in list(checkpoint.rglob("*")) + list(traces.rglob("*")):
        if path.is_file():
            assert sentinel not in path.read_text(encoding="utf-8", errors="ignore")

    after = {
        path.relative_to(vault): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in vault.rglob("*")
        if path.is_file()
    }
    assert after == before
