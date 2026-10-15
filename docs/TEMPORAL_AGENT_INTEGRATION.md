# Temporal Agent integration

This branch preserves the product implementation and focused offline tests. Historical Provider ledgers, Gold files, traces, and paid-run outputs are not part of this source branch.

## Local setup (PowerShell)

```powershell
python -m pip install -e ".[smoke]"
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -c "import linkloom; print(linkloom.__file__)"
python -m linkloom.ui --host 127.0.0.1 --port 8765
```

The printed import path must point to this checkout. The default UI start does not enable an external provider. The Temporal Agent requires explicit provider opt-in and a separate authorized run; do not reuse historical T03/T04 run identities.

## Offline verification

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m pytest -q tests/smoke/test_temporal_product_http_lifecycle.py tests/integration/test_product_application_flow.py tests/integration/test_team_decision_final_contract_communication.py tests/unit/test_ui_projection.py
```

The real HTTP acceptance runner is in `tests/smoke/run_temporal_product_http.py`; it depends on the focused smoke and integration helpers committed here. The preserved historical batches are stopped and are not reusable authorization.
