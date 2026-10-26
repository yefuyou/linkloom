# Contributing to LinkLoom

Start with the [product walkthrough](docs/V1_DEMO.md), the
[source reading map](docs/README.md), and the
[product architecture](docs/architecture/PRODUCT_ARCHITECTURE.md).
The [roadmap](docs/PRODUCT_ROADMAP.md) describes product direction.

## Report a problem or suggest an improvement

[Open an issue](https://github.com/yefuyou/linkloom/issues/new) with:

- the problem you were trying to solve;
- your OS, Python version, LinkLoom commit and launch command;
- minimal reproduction steps using synthetic meeting notes;
- expected and observed results, including the visible status;
- a screenshot or short log when useful.

Remove API keys, private notes and personal information before sharing.
Label screenshots or results that use offline preset responses.

## Make a code contribution

Use Python 3.11+ and an isolated virtual environment. From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m linkloom.ui --database work/contributor-demo.sqlite --offline-task-demo
```

Small documentation fixes and reproducible bug reports are welcome. For a new
capability or architecture change, discuss the scope in an issue before building
it. Keep fixes focused and explain the observable behavior and validation in
the pull request. Follow the repository's [Agent rules](AGENTS.md), whether
contributing by hand or with an Agent.

For focused demo tests, enable UTF-8 in child Python processes and run:

```powershell
# PowerShell; use `export PYTHONUTF8=1` in bash
$env:PYTHONUTF8 = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
python -m pytest tests/unit/test_ui_demo.py tests/integration/test_m3p_product_review.py tests/integration/test_ui_http_server.py -q
```

See the [developer map](docs/README.md#focused-offline-verification) for more
contracts. Some optional smoke checks need dependencies beyond the dev extra.
These focused checks use synthetic data and fake adapters.
Run the relevant tests for your change; the configured
[offline PR workflow](.github/workflows/offline-pr.yml) owns CI checks.
Do not add a paid-provider run as a prerequisite for a documentation fix.

Preserve the review boundary: extracted candidates and task relationship
suggestions are not approved business facts. Keep workspace scope, source
evidence and explicit provider permissions intact. Real vault writeback is
outside the current product flow.
