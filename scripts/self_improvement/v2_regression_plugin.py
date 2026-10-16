"""Pytest instrumentation for the Self-Improvement V2 paired regression.

The plugin is deliberately a test-only seam.  In baseline mode it records
pytest outcomes and leaves production objects untouched.  In candidate mode
it wraps the original ``RuntimeEngine._build_runtime_agent_adapter`` method
and installs the validated V1 ``ExperimentalQueryStrategy`` on the returned
adapter instance.  Direct ``RuntimeAgentAdapter`` construction therefore
remains unchanged and is reported as not exercising the candidate strategy.

The module is loaded as a top-level pytest plugin from a separate lab path so
that the source under test is always the immutable baseline archive.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


_ORIGINAL_BUILD: Any | None = None
_PATCHED_ENGINE: Any | None = None
_STRATEGY_INJECTIONS = 0
_ENGINE_BUILDS = 0
_PLUGIN_ERROR: str | None = None
_COLLECTED: list[str] = []
_RESULTS: dict[str, dict[str, Any]] = {}
_COLLECTION_ERRORS: list[dict[str, Any]] = []
_DESELECTED: list[str] = []
_CONFIG: Any | None = None
_MODE = "baseline"


def _reset_state() -> None:
    global _ORIGINAL_BUILD, _PATCHED_ENGINE, _STRATEGY_INJECTIONS
    global _ENGINE_BUILDS, _PLUGIN_ERROR, _COLLECTED, _RESULTS
    global _COLLECTION_ERRORS, _DESELECTED, _CONFIG, _MODE
    _ORIGINAL_BUILD = None
    _PATCHED_ENGINE = None
    _STRATEGY_INJECTIONS = 0
    _ENGINE_BUILDS = 0
    _PLUGIN_ERROR = None
    _COLLECTED = []
    _RESULTS = {}
    _COLLECTION_ERRORS = []
    _DESELECTED = []
    _CONFIG = None
    _MODE = "baseline"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load baseline module: {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses and a few baseline helpers resolve ``__module__`` through
    # sys.modules while executing.  Register the isolated module before exec;
    # this does not replace any production import because the name is unique.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _baseline_root(config: Any | None = None) -> Path | None:
    if config is not None:
        value = config.getoption("--v2-baseline-root", default=None)
        if value:
            return Path(value).resolve()
    value = os.environ.get("LINKLOOM_V2_BASELINE_ROOT")
    if value:
        return Path(value).resolve()
    return None


def _candidate_from_config(path_value: str | None, baseline_root: Path | None) -> Any:
    if not path_value:
        raise ValueError("candidate mode requires --v2-candidate-config")
    config_path = Path(path_value).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"candidate config does not exist: {config_path}")
    value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("candidate config must be a JSON object")
    if baseline_root is not None:
        contracts_path = baseline_root / "scripts" / "self_improvement" / "contracts.py"
        contracts = _load_module(contracts_path, "_linkloom_v2_baseline_contracts")
    else:
        # This fallback is only useful when the plugin is exercised directly;
        # paired regression runs always provide an archive root.
        from scripts.self_improvement.contracts import Candidate  # type: ignore

        contracts = ModuleType("_linkloom_v2_fallback_contracts")
        contracts.Candidate = Candidate
    return contracts.Candidate.parse(value)


def _load_experimental_strategy(baseline_root: Path | None) -> type[Any]:
    if baseline_root is None:
        from scripts.self_improvement.policy import ExperimentalQueryStrategy  # type: ignore

        return ExperimentalQueryStrategy
    policy_path = baseline_root / "scripts" / "self_improvement" / "policy.py"
    policy = _load_module(policy_path, "_linkloom_v2_baseline_policy")
    return policy.ExperimentalQueryStrategy


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("linkloom-self-improvement-v2")
    group.addoption(
        "--v2-regression-mode",
        action="store",
        choices=("baseline", "candidate"),
        default=None,
        help="test-only V2 regression instrumentation mode",
    )
    group.addoption(
        "--v2-candidate-config",
        action="store",
        default=None,
        help="JSON Candidate config used by candidate-mode instrumentation",
    )
    group.addoption(
        "--v2-baseline-root",
        action="store",
        default=None,
        help="immutable baseline source root used for lab imports",
    )
    group.addoption(
        "--v2-plugin-report",
        action="store",
        default=None,
        help="JSON path for test-only paired regression observations",
    )


def _option(config: Any, name: str, env: str, default: str | None = None) -> str | None:
    value = config.getoption(name, default=None)
    if value is None:
        value = os.environ.get(env)
    return value if value not in (None, "") else default


def _install_candidate_patch(config: Any) -> None:
    global _ORIGINAL_BUILD, _PATCHED_ENGINE, _STRATEGY_INJECTIONS
    global _ENGINE_BUILDS, _PLUGIN_ERROR, _CONFIG, _MODE
    _MODE = _option(config, "--v2-regression-mode", "LINKLOOM_V2_MODE", "baseline") or "baseline"
    _CONFIG = config
    if _MODE != "candidate":
        return
    baseline_root = _baseline_root(config)
    try:
        candidate_path = _option(config, "--v2-candidate-config", "LINKLOOM_V2_CONFIG")
        candidate = _candidate_from_config(candidate_path, baseline_root)
        strategy_type = _load_experimental_strategy(baseline_root)
        from linkloom.runtime.graph import RuntimeEngine

        original = RuntimeEngine._build_runtime_agent_adapter
        _ORIGINAL_BUILD = original
        _PATCHED_ENGINE = RuntimeEngine

        def build_with_candidate(self: Any) -> Any:
            global _ENGINE_BUILDS, _STRATEGY_INJECTIONS
            _ENGINE_BUILDS += 1
            adapter = original(self)
            # This is deliberately per-instance.  It does not modify the
            # RuntimeAgentAdapter class, the production default, or ToolRuntime.
            adapter._subgoal_query_strategy = strategy_type(candidate.query_mode)
            _STRATEGY_INJECTIONS += 1
            return adapter

        build_with_candidate.__name__ = "_v2_candidate_build_runtime_agent_adapter"
        build_with_candidate.__doc__ = (
            "Test-only V2 candidate wrapper around the original RuntimeEngine seam."
        )
        build_with_candidate.__wrapped__ = original
        build_with_candidate.__v2_test_instrumentation__ = True
        RuntimeEngine._build_runtime_agent_adapter = build_with_candidate
    except Exception as error:  # preserve pytest collection; report the cause
        _PLUGIN_ERROR = f"{type(error).__name__}: {error}"


def pytest_configure(config: Any) -> None:
    _reset_state()
    _install_candidate_patch(config)


def pytest_collection_finish(session: Any) -> None:
    _COLLECTED[:] = [item.nodeid for item in session.items]


def pytest_deselected(items: list[Any]) -> None:
    _DESELECTED.extend(item.nodeid for item in items)


def pytest_collectreport(report: Any) -> None:
    if getattr(report, "failed", False):
        longrepr = str(getattr(report, "longrepr", ""))
        nodeid = getattr(report, "nodeid", None)
        if not nodeid:
            collector = getattr(report, "collector", None)
            nodeid = getattr(collector, "nodeid", "")
        _COLLECTION_ERRORS.append(
            {
                "nodeid": nodeid,
                "outcome": "collection",
                "message": longrepr,
            }
        )


def pytest_runtest_logreport(report: Any) -> None:
    if getattr(report, "when", None) != "call":
        # Setup/teardown failures have no call report; retain them as the
        # node's execution status so attribution does not silently call them
        # passed.
        if getattr(report, "failed", False):
            row = _RESULTS.setdefault(report.nodeid, {"nodeid": report.nodeid})
            row.update(
                {
                    "status": "error",
                    "phase": report.when,
                    "duration": float(getattr(report, "duration", 0.0) or 0.0),
                    "longrepr": str(getattr(report, "longrepr", "")),
                }
            )
        return
    status = "passed" if report.passed else "failed" if report.failed else "skipped"
    _RESULTS[report.nodeid] = {
        "nodeid": report.nodeid,
        "status": status,
        "phase": "call",
        "duration": float(getattr(report, "duration", 0.0) or 0.0),
        "longrepr": str(getattr(report, "longrepr", "")) if report.failed else "",
    }


def _report_path(config: Any) -> Path | None:
    value = _option(config, "--v2-plugin-report", "LINKLOOM_V2_PLUGIN_REPORT")
    return Path(value).resolve() if value else None


def _report_payload(config: Any, exitstatus: int | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": "self-improvement-v2-pytest-observation/v1",
        "mode": _MODE,
        "candidate_config_sha256": None,
        "collected": list(dict.fromkeys(_COLLECTED)),
        "deselected": list(dict.fromkeys(_DESELECTED)),
        "results": list(_RESULTS.values()),
        "collection_errors": list(_COLLECTION_ERRORS),
        "strategy_injections": _STRATEGY_INJECTIONS,
        "runtime_engine_builds": _ENGINE_BUILDS,
        "strategy_exercised": _STRATEGY_INJECTIONS > 0,
        "plugin_error": _PLUGIN_ERROR,
    }
    config_path = _option(config, "--v2-candidate-config", "LINKLOOM_V2_CONFIG")
    if config_path:
        candidate_path = Path(config_path)
        if candidate_path.is_file():
            try:
                payload["candidate_config_sha256"] = _sha256(candidate_path)
            except OSError:
                payload["candidate_config_sha256"] = "unavailable"
    if exitstatus is not None:
        payload["exitstatus"] = int(exitstatus)
    return payload


def _write_report(config: Any, exitstatus: int | None = None) -> None:
    path = _report_path(config)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each subprocess receives a unique report path.  Refuse overwrite so a
    # stale or accidentally reused report cannot be mistaken for this run.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(_report_payload(config, exitstatus), stream, ensure_ascii=False, indent=2)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    # ``exitstatus`` is deliberately recorded separately from node outcomes;
    # collection and environment failures can end the process before a test
    # result exists.
    try:
        _write_report(session.config, exitstatus)
    except Exception as error:
        # Pytest is already finishing.  Keep the instrumentation failure
        # visible in stderr without replacing the test process status.
        sys.stderr.write(f"V2 regression plugin report failed: {error}\n")


def pytest_unconfigure(config: Any) -> None:
    global _ORIGINAL_BUILD, _PATCHED_ENGINE
    if _PATCHED_ENGINE is not None and _ORIGINAL_BUILD is not None:
        _PATCHED_ENGINE._build_runtime_agent_adapter = _ORIGINAL_BUILD
    _PATCHED_ENGINE = None
    _ORIGINAL_BUILD = None


__all__ = ["pytest_addoption", "pytest_configure", "pytest_sessionfinish"]
