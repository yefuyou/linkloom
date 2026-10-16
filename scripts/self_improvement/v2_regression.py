"""Bounded Base/Candidate regression attribution for Self-Improvement V2.

Both sides run the same tracked tests from an immutable archive of the exact
V1 base commit. Candidate behavior is supplied only by
``v2_regression_plugin``; this module never edits production source and never
claims that a collect-only full-repository attempt is green.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4
import xml.etree.ElementTree as ET


BASE_COMMIT = "92c0fd282f8c33dfe29471aa70b9d7cfbf6e1d75"
SCHEMA_VERSION = "self-improvement-v2-regression/v1"
OBSERVATION_SCHEMA = "self-improvement-v2-pytest-observation/v1"

STAGE_CORE = "Core Regression"
STAGE_HISTORICAL = "Legacy Historical Regression"
STAGE_DIFFERENTIAL = "Candidate Differential Regression"
STAGE_FULL = "Full Repository Attempt"

CORE_TEST_PATHS = (
    "tests/unit/test_claim_grounding.py",
    "tests/unit/test_source_identity_contract.py",
    "tests/unit/test_tool_runtime.py",
    "tests/unit/test_tool_ledger_runtime.py",
    "tests/unit/test_phase3_task_plan.py",
    "tests/unit/test_p84_recovery.py",
    "tests/unit/test_graceful_partial_recovery.py",
    "tests/unit/test_recovery_policy.py",
    "tests/unit/test_permission_gate.py",
    "tests/unit/test_strategy_contract.py",
    "tests/unit/test_strategy_integrity.py",
    "tests/unit/test_runtime_v2_grounding_visibility.py",
    "tests/unit/test_runtime_v2_multi_action.py",
    "tests/integration/test_bounded_multihop_flow.py",
    "tests/integration/test_retrieval_tool_runtime.py",
    "tests/integration/test_product_application_flow.py",
    "tests/integration/test_product_segment_recovery.py",
)

# These are intentionally ordinary pytest paths. The four known historical
# collection failures remain visible; no unconditional skip hides them.
HISTORICAL_TEST_PATHS = (
    "tests/unit/test_gemini_count_tokens_compat.py",
    "tests/unit/test_gemini_generation_failure_diagnostics.py",
    "tests/unit/test_gemini_nullable_json_schema.py",
    "tests/unit/test_p85_gemini_api_adapter.py",
    "tests/unit/test_p85_model_provider_contracts.py",
    "tests/unit/test_memoryagentbench_g31_fixed50.py",
    "tests/unit/test_memoryagentbench_g32_heldout.py",
    "tests/unit/test_memoryagentbench_g3_pilot.py",
    "tests/integration/test_stage_c_end_to_end_loop.py",
)

KNOWN_MISSING_SCRIPTS = (
    "run_memoryagentbench_g3_pilot.py",
    "run_stage_c_context_memory_loop.py",
)

_ENV_ERROR = re.compile(
    r"ModuleNotFoundError|ImportError|FileNotFoundError|PermissionError|"
    r"No module named|No such file|DLL load failed|TimeoutExpired|timed? ?out", re.I
)
_SECRET = re.compile(r"api[_-]?key|access[_-]?token|secret|password|credential", re.I)
_LIVE = re.compile(r"(?:^|_)(?:live|real|provider|network|run[_-]?live)(?:$|_)", re.I)


def _io_path(path: Path) -> Path:
    path = Path(path).resolve()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\"+str(path))
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _io_path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


@dataclass(frozen=True)
class BaselineArchive:
    commit: str
    root: Path
    source: Path
    tar: Path
    manifest: Path
    archive_sha256: str
    file_count: int

    # Keep the descriptive names used by the regression report and by callers
    # that treat the archive as a small value object.  ``root/source/tar`` are
    # retained as the compact internal fields so old helpers remain simple.
    @property
    def archive_root(self) -> Path:
        return self.root

    @property
    def source_root(self) -> Path:
        return self.source

    @property
    def archive_path(self) -> Path:
        return self.tar

    @property
    def manifest_path(self) -> Path:
        return self.manifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit, "archive_root": str(self.root), "source_root": str(self.source),
            "archive_path": str(self.tar), "manifest_path": str(self.manifest),
            "archive_sha256": self.archive_sha256, "file_count": self.file_count, "immutable": True,
        }


def _extract_git_archive(tar_path: Path, target: Path) -> list[str]:
    """Extract the trusted commit, including tracked data used by product tests."""
    with tarfile.open(tar_path, "r") as archive:
        members = archive.getmembers()
        names = [member.name.replace("\\", "/") for member in members]
        target = target.resolve()
        for member in members:
            path = (target / member.name).resolve()
            if target != path and target not in path.parents:
                raise ValueError(f"archive member escapes source root: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"refusing archive link: {member.name}")
        for member in members:
            path = (target / member.name).resolve()
            _io_path(path if member.isdir() else path.parent).mkdir(parents=True, exist_ok=True)
            # Every member has already passed the path and link checks above.
            # Use the hardened extractor where available while retaining the
            # project's Python 3.11 compatibility.
            if sys.version_info >= (3, 12):
                archive.extract(member, _io_path(target), filter="data")
            else:
                archive.extract(member, _io_path(target))
    return names


def create_immutable_baseline_archive(
    project_root: Path | str,
    *, base_commit: str = BASE_COMMIT, work_root: Path | str | None = None,
) -> BaselineArchive:
    """Create a fresh, never-overwritten archive below ignored ``work/b92``."""

    project_root = Path(project_root).resolve()
    work_root = Path(work_root).resolve() if work_root else project_root / "work" / "b92"
    work_root.mkdir(parents=True, exist_ok=True)
    resolved = _git(project_root, "rev-parse", "--verify", f"{base_commit}^{{commit}}")
    if resolved != base_commit:
        raise ValueError(f"base commit verification failed: {resolved}")
    root = work_root / uuid4().hex[:8]
    root.mkdir(exist_ok=False)
    tar_path, source, manifest_path = root / "base.tar", root / "s", root / "manifest.json"
    source.mkdir()
    subprocess.run(
        ["git", "archive", "--format=tar", f"--output={tar_path}", base_commit],
        cwd=project_root, check=True, capture_output=True,
    )
    names = _extract_git_archive(tar_path, source)
    digest = sha256(tar_path)
    file_count = len([name for name in names if not name.endswith("/")])
    _write(manifest_path, {
        "schema_version": "self-improvement-v2-baseline-archive/v1", "commit": base_commit,
        "resolved_commit": resolved, "archive_sha256": digest,
        "file_count": file_count,
        "members_sha256": _hash_json(names), "immutable": True, "created_without_cleanup": True,
    })
    return BaselineArchive(base_commit, root, source, tar_path, manifest_path, digest, file_count)


def verify_immutable_baseline_archive(archive: BaselineArchive) -> dict[str, Any]:
    manifest = json.loads(archive.manifest.read_text(encoding="utf-8"))
    if manifest.get("commit") != archive.commit or manifest.get("resolved_commit") != archive.commit:
        raise ValueError("baseline archive commit manifest changed")
    digest = sha256(archive.tar)
    if digest != archive.archive_sha256 or digest != manifest.get("archive_sha256"):
        raise ValueError("baseline archive digest changed")
    if not archive.source.is_dir():
        raise FileNotFoundError(f"baseline source missing: {archive.source}")
    verify_extracted_source(archive.source,archive.tar)
    return manifest


def verify_extracted_source(source: Path, archive_path: Path) -> None:
    """Check tracked inputs against the tar; new pytest caches are irrelevant."""
    with tarfile.open(archive_path) as snapshot:
        for member in snapshot.getmembers():
            if not member.isfile(): continue
            expected = snapshot.extractfile(member)
            path = _io_path(source/member.name)
            if expected is None or not path.is_file() or hashlib.sha256(expected.read()).hexdigest() != sha256(path):
                raise ValueError("baseline extracted source changed: "+member.name)


def _candidate(value: Mapping[str, Any], baseline_root: Path | None = None) -> Any:
    if baseline_root is None:
        from scripts.self_improvement.contracts import Candidate
    else:
        path = baseline_root / "scripts" / "self_improvement" / "contracts.py"
        module_name = f"_v2_archive_contracts_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load archived Candidate contract: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        Candidate = module.Candidate
    return Candidate.parse(dict(value))


def load_candidate_config(path: Path | str, *, baseline_root: Path | None = None) -> Any:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("candidate config must be a JSON object")
    return _candidate(value, baseline_root)


def default_candidate(*, baseline_root: Path | None = None) -> Any:
    return _candidate({
        "id": "v2-relation-terms", "change": "Use bounded relation terms for dependent query wording",
        "query_mode": "relation_terms", "read_mode": "all", "limit": 4,
    }, baseline_root)


def _candidate_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("candidate must be a Candidate or mapping")
    return dict(value)


def _candidate_config_path(value: Any) -> bool:
    return isinstance(value, (str, Path, os.PathLike))


def sanitized_environment(
    source: Path, plugin_root: Path, *, mode: str, candidate_config: Path | None,
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    raw = dict(base_environment or os.environ)
    env = {
        key: value for key, value in raw.items()
        if not _SECRET.search(key) and not key.upper().endswith("API_KEY")
        and not _LIVE.search(key) and not key.upper().startswith("LINKLOOM_RUN_")
    }
    env.update({
        "PYTHONUTF8": "1", "LINKLOOM_RETRIEVAL_MODE": "bm25", "LINKLOOM_V2_MODE": mode,
        "LINKLOOM_V2_BASELINE_ROOT": str(source.resolve()),
        "PYTHONPATH": os.pathsep.join([
            str((source / "src").resolve()), str(source.resolve()), str(plugin_root.resolve())
        ]),
    })
    if candidate_config is None:
        env.pop("LINKLOOM_V2_CONFIG", None)
    else:
        env["LINKLOOM_V2_CONFIG"] = str(candidate_config.resolve())
    return env


def environment_fingerprint(env: Mapping[str, str]) -> dict[str, Any]:
    values = {key: value for key, value in sorted(env.items()) if not _SECRET.search(key)}
    return {"values": values, "sha256": _hash_json(values)}


def _paired_environment(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    left, right = dict(base.get("values", {})), dict(candidate.get("values", {}))
    deliberate = {"LINKLOOM_V2_MODE", "LINKLOOM_V2_CONFIG", "LINKLOOM_V2_PLUGIN_REPORT"}
    for key in deliberate:
        left.pop(key, None); right.pop(key, None)
    return {"equal_after_selector_removal": left == right,
            "deliberate_differences": sorted(deliberate & (set(base.get("values", {})) | set(candidate.get("values", {}))))}


@dataclass(frozen=True)
class StageSpec:
    name: str
    paths: tuple[str, ...]
    collect_only: bool = False


@dataclass
class ProcessResult:
    mode: str
    command: list[str]
    environment: dict[str, Any]
    log_path: str
    junit_path: str
    plugin_report_path: str
    returncode: int | None
    timed_out: bool
    report: dict[str, Any]
    duration_seconds: float = 0.0


def stage_specs(*, core_paths: Sequence[str] = CORE_TEST_PATHS,
                historical_paths: Sequence[str] = HISTORICAL_TEST_PATHS) -> dict[str, StageSpec]:
    return {STAGE_CORE: StageSpec(STAGE_CORE, tuple(core_paths)),
            STAGE_HISTORICAL: StageSpec(STAGE_HISTORICAL, tuple(historical_paths)),
            STAGE_FULL: StageSpec(STAGE_FULL, ("tests",), True)}


def _pytest_command(stage: StageSpec, *, mode: str, source: Path, paths: Sequence[str], temp: Path,
                    junit: Path, plugin: Path, candidate_config: Path | None) -> list[str]:
    command = [sys.executable, "-m", "pytest", *paths, "-q", "--continue-on-collection-errors",
               "--disable-warnings", f"--basetemp={temp}", f"--junitxml={junit}", "-p",
               "v2_regression_plugin", "--v2-regression-mode", mode,
               f"--v2-baseline-root={source}", f"--v2-plugin-report={plugin}"]
    if stage.collect_only: command.append("--collect-only")
    if mode == "candidate" and candidate_config is not None:
        command.append(f"--v2-candidate-config={candidate_config}")
    return command


def _junit(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"collected": [], "results": [], "collection_errors": [], "available": False}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        return {"collected": [], "results": [], "collection_errors": [{"message": str(error)}], "available": False}
    results = []
    for case in root.iter("testcase"):
        nodeid = case.attrib.get("nodeid") or f"{case.attrib.get('file', case.attrib.get('classname', ''))}::{case.attrib.get('name', '')}"
        if case.find("failure") is not None: status, detail = "failed", case.findtext("failure", "")
        elif case.find("error") is not None: status, detail = "error", case.findtext("error", "")
        elif case.find("skipped") is not None: status, detail = "skipped", ""
        else: status, detail = "passed", ""
        results.append({"nodeid": nodeid, "status": status, "longrepr": detail})
    return {"collected": [row["nodeid"] for row in results], "results": results,
            "collection_errors": [], "available": True}


def _read_report(path: Path, junit: Path) -> dict[str, Any]:
    if not path.exists(): return _junit(junit)
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema_version") != OBSERVATION_SCHEMA:
        raise ValueError(f"unexpected plugin report schema: {path}")
    report["available"] = True
    return report


def _run(command: list[str], *, cwd: Path, env: Mapping[str, str], mode: str, log: Path,
         junit: Path, plugin: Path, timeout: float) -> ProcessResult:
    started = time.monotonic(); timed_out, returncode = False, None
    with log.open("x", encoding="utf-8") as stream:
        try:
            returncode = subprocess.run(command, cwd=cwd, env=dict(env), stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    report = _read_report(plugin, junit)
    report.update(exitstatus=returncode, timed_out=timed_out,
                  duration_seconds=time.monotonic() - started)
    return ProcessResult(mode, command, environment_fingerprint(env), str(log), str(junit),
                         str(plugin), returncode, timed_out, report,
                         duration_seconds=float(report.get("duration_seconds", 0.0)))


def _failure(row: Mapping[str, Any] | None) -> str:
    text = re.sub(r"\s+", " ", str((row or {}).get("longrepr") or (row or {}).get("message") or "")).strip()
    text = re.sub(r"[A-Za-z]:\\[^\s'\"]+|/[^\s'\"]+", "<PATH>", text)
    # Pytest reprs commonly contain object identities (providers, stores,
    # monkeypatch helpers).  They are run-specific noise, not a changed
    # assertion.  Normalize them before comparing paired failures.
    return re.sub(r"0x[0-9a-fA-F]+", "<ADDR>", text)


def _map(report: Mapping[str, Any], key: str = "results") -> dict[str, dict[str, Any]]:
    return {str(row.get("nodeid")): dict(row) for row in report.get(key, []) if row.get("nodeid")}


def classify_pair(baseline: ProcessResult, candidate: ProcessResult) -> list[dict[str, Any]]:
    """Attribute each observed node without assuming every failure is old."""

    br, cr = _map(baseline.report), _map(candidate.report)
    bc, cc = _map(baseline.report, "collection_errors"), _map(candidate.report, "collection_errors")
    nodes = sorted(set(br) | set(cr) | set(bc) | set(cc) |
                   set(baseline.report.get("collected", [])) | set(candidate.report.get("collected", [])))
    pairs = []
    for nodeid in nodes:
        b, c = br.get(nodeid), cr.get(nodeid)
        bs = b.get("status") if b else "collection" if nodeid in bc else "not_executed"
        cs = c.get("status") if c else "collection" if nodeid in cc else "not_executed"
        bf, cf = _failure(b or bc.get(nodeid)), _failure(c or cc.get(nodeid))
        # Keep collection and execution gaps visible before looking at failure
        # text.  A skipped node is evidence that the pair did not execute the
        # same behavior, so it must not be reported as a candidate regression.
        if bs == "collection" or cs == "collection":
            category = "collection"
        elif bs == "not_executed" or cs == "not_executed" or "skipped" in {bs, cs}:
            category = "not_executed"
        elif bs in {"failed", "error"} and cs in {"failed", "error"}:
            if _ENV_ERROR.search(bf) and _ENV_ERROR.search(cf):
                category = "environment"
            elif bf and bf == cf:
                category = "baseline_existing"
            else:
                # A failure that was already present is not a new candidate
                # failure merely because a volatile traceback detail changed.
                # Keep this transition visible for review without inflating
                # the candidate-caused count.
                category = "changed_existing_failure"
        elif bs in {"failed", "error"} and cs == "passed":
            category = "environment" if _ENV_ERROR.search(bf) else "baseline_existing"
        elif bs == "passed" and cs in {"failed", "error"}:
            category = "environment" if _ENV_ERROR.search(cf) else "candidate_caused"
        elif bs == cs == "passed":
            category = "pass"
        else:
            category = "new"
        pairs.append({"nodeid": nodeid, "baseline_status": bs, "candidate_status": cs,
                      "classification": category, "transition": f"{bs}_to_{cs}",
                      "baseline_failure": bf or None, "candidate_failure": cf or None,
                      "evidence": {"baseline_reported": bool(b or nodeid in bc),
                                   "candidate_reported": bool(c or nodeid in cc),
                                   "baseline_timed_out": baseline.timed_out,
                                   "candidate_timed_out": candidate.timed_out}})
    return pairs


def _summary(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in pairs:
        key = str(row.get("classification")); counts[key] = counts.get(key, 0) + 1
    candidate_caused = sorted(
        str(row["nodeid"]) for row in pairs if row.get("classification") == "candidate_caused"
    )
    new = sorted(str(row["nodeid"]) for row in pairs if row.get("classification") == "new")
    changed_existing = sorted(
        str(row["nodeid"])
        for row in pairs
        if row.get("classification") == "changed_existing_failure"
    )
    return {"counts": counts, "baseline_existing": counts.get("baseline_existing", 0),
            "new": counts.get("new", 0), "candidate_caused": counts.get("candidate_caused", 0),
            "changed_existing_failure": counts.get("changed_existing_failure", 0),
            "not_executed": counts.get("not_executed", 0), "collection": counts.get("collection", 0),
            "environment": counts.get("environment", 0), "stable_pass": counts.get("pass", 0),
            "candidate_caused_nodes": candidate_caused, "new_nodes": new,
            "changed_existing_failure_nodes": changed_existing,
            "candidate_new_failures": sorted(set(candidate_caused) | set(new)),
            "candidate_has_new_failure": bool(candidate_caused or new)}


def _missing_scripts(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    text = "\n".join(str(row.get("message", "")) for report in reports for row in report.get("collection_errors", []))
    observed = [name for name in KNOWN_MISSING_SCRIPTS if name in text]
    return {"known": list(KNOWN_MISSING_SCRIPTS), "observed": observed,
            "unobserved": [name for name in KNOWN_MISSING_SCRIPTS if name not in observed],
            "evidence_sha256": hashlib.sha256(text.encode()).hexdigest()}


def _stage(stage: StageSpec, archive: BaselineArchive, candidate_config: Path, out: Path,
           plugin_root: Path, timeout: float) -> dict[str, Any]:
    root = out / re.sub(r"[^A-Za-z0-9]+", "-", stage.name.casefold()).strip("-")
    root.mkdir(parents=True, exist_ok=False)
    # Keep Windows artifact paths within the same practical limit on both sides.
    temp_root = Path(__file__).resolve().parents[2] / "work" / "t" / uuid4().hex[:6]
    temp_root.mkdir(parents=True, exist_ok=False)
    base_temp, cand_temp = temp_root / "b", temp_root / "c"
    assert len(base_temp.name) == len(cand_temp.name)
    bp, cp = root / "baseline.plugin.json", root / "candidate.plugin.json"
    bj, cj = root / "baseline.junit.xml", root / "candidate.junit.xml"
    source = archive.source
    verify_immutable_baseline_archive(archive)
    be = sanitized_environment(source, plugin_root, mode="baseline", candidate_config=None)
    ce = sanitized_environment(source, plugin_root, mode="candidate", candidate_config=candidate_config)
    be["LINKLOOM_V2_PLUGIN_REPORT"], ce["LINKLOOM_V2_PLUGIN_REPORT"] = str(bp.resolve()), str(cp.resolve())
    bc = _pytest_command(stage, mode="baseline", source=source, paths=stage.paths, temp=base_temp,
                         junit=bj, plugin=bp, candidate_config=None)
    cc = _pytest_command(stage, mode="candidate", source=source, paths=stage.paths, temp=cand_temp,
                         junit=cj, plugin=cp, candidate_config=candidate_config)
    base = _run(bc, cwd=source, env=be, mode="baseline", log=root / "baseline.log", junit=bj, plugin=bp, timeout=timeout)
    verify_immutable_baseline_archive(archive)
    cand = _run(cc, cwd=source, env=ce, mode="candidate", log=root / "candidate.log", junit=cj, plugin=cp, timeout=timeout)
    verify_immutable_baseline_archive(archive)
    pairs = classify_pair(base, cand)
    missing_scripts = _missing_scripts([base.report, cand.report])
    report = {"schema_version": "self-improvement-v2-regression-stage/v1", "stage": stage.name,
              "paths": list(stage.paths), "collect_only": stage.collect_only,
              "baseline": base.__dict__, "candidate": cand.__dict__, "pairs": pairs,
              "summary": _summary(pairs), "missing_scripts": missing_scripts,
              "environment_pairing": _paired_environment(base.environment, cand.environment),
              "strategy_transfer": {"candidate_strategy_exercised": bool(cand.report.get("strategy_exercised")),
                "not_exercising_strategy": not bool(cand.report.get("strategy_exercised")),
                "runtime_engine_builds": cand.report.get("runtime_engine_builds", 0),
                "note": "Direct RuntimeAdapter tests remain unchanged when the RuntimeEngine seam is unused."}}
    if stage.collect_only:
        report["full_repo_attempt"] = {"status": "not_full_green", "mode": "collect_only",
            "not_executed_nodes": sorted(set(base.report.get("collected", [])) | set(cand.report.get("collected", []))),
            "missing_scripts": missing_scripts,
            "reason": "Collection inventory is evidence; no full repository execution was claimed."}
    return report


def _not_requested(name: str, reason: str) -> dict[str, Any]:
    return {"schema_version": "self-improvement-v2-regression-stage/v1", "stage": name,
            "status": "not_requested", "reason": reason, "pairs": [], "summary": _summary([]),
            "missing_scripts": {"known": list(KNOWN_MISSING_SCRIPTS), "observed": [],
                                "unobserved": list(KNOWN_MISSING_SCRIPTS)}}


def run_regression(
    project_root: Path | str, *, candidate_config: Path | str | Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | Any | None = None, output_root: Path | str | None = None,
    work_root: Path | str | None = None, plugin_root: Path | str | None = None,
    stages: Sequence[str] = (STAGE_CORE, STAGE_HISTORICAL), run_full_collect: bool = False,
    timeout_seconds: float = 900.0, core_paths: Sequence[str] = CORE_TEST_PATHS,
    historical_paths: Sequence[str] = HISTORICAL_TEST_PATHS,
) -> Path:
    """Run requested paired stages and return a fresh ``report.json``."""

    project_root, plugin_root = Path(project_root).resolve(), Path(plugin_root or Path(__file__).parent).resolve()
    archive = create_immutable_baseline_archive(project_root, work_root=work_root)
    verify_immutable_baseline_archive(archive)
    if candidate_config is not None and candidate is not None:
        raise ValueError("provide candidate_config or candidate, not both")
    if candidate_config is None:
        candidate_value = candidate or default_candidate(baseline_root=archive.source)
        parsed_candidate = _candidate(_candidate_dict(candidate_value), baseline_root=archive.source)
        candidate_config = archive.root / "candidate.json"
        _write(Path(candidate_config), _candidate_dict(parsed_candidate))
    elif _candidate_config_path(candidate_config):
        candidate_config = Path(candidate_config).resolve()
        parsed_candidate = load_candidate_config(candidate_config, baseline_root=archive.source)
    else:
        # The V2 runner passes ``Candidate.to_dict()`` directly.  Materialize
        # that mapping inside this fresh archive so both subprocesses consume
        # the same validated, immutable experiment input.
        parsed_candidate = _candidate(_candidate_dict(candidate_config), baseline_root=archive.source)
        candidate_config = archive.root / "candidate.json"
        _write(Path(candidate_config), _candidate_dict(parsed_candidate))
    output = Path(output_root).resolve() if output_root else archive.root / "report"
    output.mkdir(parents=True, exist_ok=False)
    specs = stage_specs(core_paths=core_paths, historical_paths=historical_paths)
    allowed_stages = {STAGE_CORE, STAGE_HISTORICAL, STAGE_FULL}
    requested = set(stages)
    unknown = requested - allowed_stages
    if unknown:
        raise ValueError(f"unknown regression stage(s): {sorted(unknown)}")
    run_full_collect = run_full_collect or STAGE_FULL in requested
    stage_reports = {
        name: (_stage(specs[name], archive, Path(candidate_config), output, plugin_root, timeout_seconds)
               if name in requested else _not_requested(name, "Stage was not requested."))
        for name in (STAGE_CORE, STAGE_HISTORICAL)
    }
    pairs = [row for name in (STAGE_CORE, STAGE_HISTORICAL) for row in stage_reports[name].get("pairs", [])]
    stage_reports[STAGE_DIFFERENTIAL] = {"schema_version": "self-improvement-v2-regression-stage/v1",
        "stage": STAGE_DIFFERENTIAL, "status": "derived_from_paired_runs",
        "source_stages": [name for name in (STAGE_CORE, STAGE_HISTORICAL) if name in requested],
        "pairs": pairs, "summary": _summary(pairs),
        "evidence_rule": "Exact paired node outcomes and normalized failure fingerprints only."}
    stage_reports[STAGE_FULL] = (_stage(specs[STAGE_FULL], archive, Path(candidate_config), output, plugin_root, timeout_seconds)
        if run_full_collect else {**_not_requested(STAGE_FULL, "Full collect-only attempt was not requested."),
          "full_repo_attempt": {"status": "not_full_green", "mode": "not_requested", "not_executed_nodes": None,
            "missing_scripts": {"known": list(KNOWN_MISSING_SCRIPTS), "observed": [], "unobserved": list(KNOWN_MISSING_SCRIPTS)},
            "reason": "No full-repository green claim is made."}})
    core_summary = stage_reports[STAGE_CORE].get("summary", _summary([]))
    historical_summary = stage_reports[STAGE_HISTORICAL].get("summary", _summary([]))
    core_new_failures = list(core_summary.get("candidate_new_failures", []))
    report = {"schema_version": SCHEMA_VERSION, "base_commit": BASE_COMMIT, "archive": archive.to_dict(),
      "candidate": {"config_path": str(candidate_config), "config_sha256": sha256(Path(candidate_config)),
        "config": _candidate_dict(candidate or load_candidate_config(candidate_config, baseline_root=archive.source)),
        "instrumentation": "pytest-only RuntimeEngine seam; production source unchanged"},
      "environment_contract": {"retrieval_mode": "bm25", "api_keys_stripped": True, "live_flags_stripped": True,
         "same_archived_source_and_tests": True, "equal_length_unique_basetemps": True}, "stages": stage_reports,
      # These fields let the V2 orchestrator consume the bounded result without
      # guessing which stage or classification represents a new core failure.
      "core_complete": bool(STAGE_CORE in requested and not core_new_failures
                            and not core_summary.get("collection")
                            and not core_summary.get("environment")
                            and not core_summary.get("not_executed")),
      "core_new_failures": core_new_failures,
      "historical_new_failures": list(historical_summary.get("candidate_new_failures", [])),
      "missing_tracked_scripts": {
          name: stage_reports[name].get("missing_scripts", {})
          for name in (STAGE_CORE, STAGE_HISTORICAL, STAGE_FULL)
      },
      "full_repo_green": False, "not_full_green": True,
      "acceptance_boundary": {"historical_failures_preserved": True, "collection_errors_deleted_or_skipped": False,
        "production_accepted_possible": False}}
    path = output / "report.json"
    _write(path, report)
    return path


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--candidate-config", type=Path); parser.add_argument("--output-root", type=Path)
    parser.add_argument("--work-root", type=Path); parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--core", action="store_true"); parser.add_argument("--historical", action="store_true")
    parser.add_argument("--full-collect", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _args()
    selected = [name for flag, name in ((args.core, STAGE_CORE), (args.historical, STAGE_HISTORICAL)) if flag]
    report = run_regression(args.project_root, candidate_config=args.candidate_config, output_root=args.output_root,
                            work_root=args.work_root, stages=selected or (STAGE_CORE, STAGE_HISTORICAL),
                            run_full_collect=args.full_collect, timeout_seconds=args.timeout_seconds)
    print(json.dumps({"report": str(report), "not_full_green": True}, ensure_ascii=False))


if __name__ == "__main__": main()


prepare_baseline_archive = create_immutable_baseline_archive
verify_baseline_archive = verify_immutable_baseline_archive
attribute_regression = classify_pair
build_pytest_command = _pytest_command

__all__ = ["BASE_COMMIT", "CORE_TEST_PATHS", "HISTORICAL_TEST_PATHS", "KNOWN_MISSING_SCRIPTS",
           "STAGE_CORE", "STAGE_HISTORICAL", "STAGE_DIFFERENTIAL", "STAGE_FULL", "BaselineArchive",
           "ProcessResult", "StageSpec", "classify_pair", "create_immutable_baseline_archive",
           "default_candidate", "environment_fingerprint", "load_candidate_config", "run_regression",
           "sanitized_environment", "stage_specs", "verify_immutable_baseline_archive"]
