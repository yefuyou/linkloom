"""Run the frozen 249-node Core regression at two Git commits.

The runner is deliberately small and offline.  It materializes both commits
from Git archives into ``work/evaluation-qualification/core``, invokes the
same Python/pytest environment serially, writes one XML report per side, and
compares every requested JUnit node without treating skipped or missing-input
cases as green.

Example::

    python scripts/evaluation_core_regression.py \
      --run-id full249-20261017 \
      --baseline-commit 37e45b169f19c4edd29db59f637e690caab31ad6 \
      --candidate-commit 499bb3733bb10c0fc5c3145da9b8a9d3d587274d

The command never selects a Provider test or supplies Provider credentials.
Its ``provider_budget`` is zero and child processes inherit no common API-key
environment variables.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[1]
DEFAULT_NODE_FILE = REPO / "tests" / "fixtures" / "evaluation_qualification" / "core_nodes.json"
DEFAULT_WORK = REPO / "work" / "evaluation-qualification" / "core"
DEFAULT_BASELINE = "37e45b169f19c4edd29db59f637e690caab31ad6"
DEFAULT_CANDIDATE = "499bb3733bb10c0fc5c3145da9b8a9d3d587274d"
EXPECTED_NODE_COUNT = 249
MAX_BATCH_CHARS = 24_000
PROVIDER_ENV_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LINKLOOM_ALLOW_EXTERNAL_PROVIDER",
)
SKIP_ENVIRONMENT_WORDS = (
    "not installed",
    "dependency",
    "requires",
    "unsupported platform",
    "not supported on this platform",
)
SKIP_INPUT_WORDS = (
    "unavailable",
    "missing",
    "historical",
    "sealed",
    "artifact",
    "fixture",
    "local source",
    "unchanged local",
)


@dataclass(frozen=True)
class Archive:
    label: str
    commit: str
    root: Path
    zip_path: Path
    zip_sha256: str


@dataclass(frozen=True)
class BatchResult:
    label: str
    index: int
    requested: tuple[str, ...]
    xml_path: Path
    log_path: Path
    command: tuple[str, ...]
    returncode: int
    output: str
    duration_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_nodes(path: Path) -> tuple[list[str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_nodes = payload.get("nodes")
    if isinstance(raw_nodes, list):
        nodes = [item if isinstance(item, str) else item.get("nodeid") for item in raw_nodes]
    else:
        nodes = []
    if any(not isinstance(node, str) or not node for node in nodes):
        raise ValueError("core node fixture contains a non-string nodeid")
    if len(nodes) != EXPECTED_NODE_COUNT:
        raise ValueError(f"expected {EXPECTED_NODE_COUNT} nodeids, found {len(nodes)}")
    if len(set(nodes)) != len(nodes):
        raise ValueError("core node fixture contains duplicate nodeids")
    return nodes, payload


def _safe_archive_member(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe archive member: {name!r}")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"archive member escapes destination: {name!r}")
    return target


def _archive_commit(label: str, commit: str, work_dir: Path, checkout_dir: Path) -> Archive:
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / f"{label}-{commit}.zip"
    if not zip_path.exists():
        subprocess.run(
            ["git", "archive", "--format=zip", "--output", str(zip_path), commit],
            cwd=REPO,
            check=True,
        )

    # Keep the extracted checkout short on Windows.  The repository contains
    # long, nested fixture paths and the legacy MAX_PATH limit would otherwise
    # make a valid Git archive fail during extraction.
    checkout_dir.mkdir(parents=True, exist_ok=True)
    base_root = checkout_dir / label[:1]
    root = base_root
    suffix = 1
    while root.exists():
        marker = root / ".evaluation-qualification-commit"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == commit:
            _verify_archive_sources(root, zip_path)
            return Archive(label, commit, root, zip_path, _sha256(zip_path))
        root = checkout_dir / f"{label[:1]}-{suffix}"
        suffix += 1
    root.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = _safe_archive_member(root, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    (root / ".evaluation-qualification-commit").write_text(commit + "\n", encoding="utf-8")
    return Archive(label, commit, root, zip_path, _sha256(zip_path))


def _verify_archive_sources(root: Path, archive_path: Path) -> dict[str, Any]:
    """Verify every original tracked byte; generated test outputs are separate."""
    with zipfile.ZipFile(archive_path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        mismatches = [m.filename for m in members
                      if not (root / m.filename).is_file()
                      or (root / m.filename).read_bytes() != archive.read(m)]
    if mismatches:
        raise ValueError(f"archived tracked sources changed: {mismatches}")
    return {"tracked_files": len(members), "mismatches": [], "verified": True}


def _batch_nodes(nodes: list[str]) -> list[tuple[str, ...]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for node in nodes:
        addition = len(node) + 1
        if current and current_size + addition > MAX_BATCH_CHARS:
            batches.append(current)
            current = []
            current_size = 0
        current.append(node)
        current_size += addition
    if current:
        batches.append(current)
    return [tuple(batch) for batch in batches]


def _pytest_nodeid(node: str) -> str:
    """Convert the frozen JUnit ``classname::name`` key to a pytest path."""
    module, separator, test_name = node.partition("::")
    if not separator:
        return node
    if module.startswith("tests."):
        module = module.replace(".", "/") + ".py"
    return f"{module}::{test_name}"


def _child_environment(archive: Archive) -> dict[str, str]:
    environment = os.environ.copy()
    for key in PROVIDER_ENV_KEYS:
        environment.pop(key, None)
    environment["LINKLOOM_PROVIDER_BUDGET"] = "0"
    environment["LINKLOOM_EXTERNAL_PROVIDER"] = "0"
    environment["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONPATH"] = os.pathsep.join(
        str(path)
        for path in (archive.root / "src", archive.root / "scripts", REPO / "scripts")
    )
    return environment


def _run_side(
    archive: Archive,
    nodes: list[str],
    label: str,
    run_dir: Path,
) -> list[BatchResult]:
    side_dir = run_dir / label
    side_dir.mkdir(parents=True, exist_ok=True)
    batches = _batch_nodes(nodes)
    results: list[BatchResult] = []
    environment = _child_environment(archive)
    for index, batch in enumerate(batches, start=1):
        xml_path = side_dir / f"batch-{index:02d}.xml"
        log_path = side_dir / f"batch-{index:02d}.log"
        basetemp = Path(tempfile.gettempdir()) / ("llr-" + uuid4().hex[:8])
        environment["LINKLOOM_PYTEST_BASETEMP"] = str(basetemp)
        command = [
            sys.executable,
            "-m",
            "pytest",
            *(_pytest_nodeid(node) for node in batch),
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "pytest_windows_tempdir",
            "-p",
            "evaluation_offline_plugin",
            "--tb=short",
            "--junitxml",
            str(xml_path),
            "--basetemp",
            str(basetemp),
        ]
        started = datetime.now(UTC)
        completed = subprocess.run(
            command,
            cwd=archive.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        duration = (datetime.now(UTC) - started).total_seconds()
        output = completed.stdout or ""
        log_path.write_text(output, encoding="utf-8")
        results.append(
            BatchResult(
                label=label,
                index=index,
                requested=batch,
                xml_path=xml_path,
                log_path=log_path,
                command=tuple(command),
                returncode=completed.returncode,
                output=output,
                duration_seconds=duration,
            )
        )
    return results


def _skip_status(message: str) -> str:
    lowered = message.lower()
    if any(word in lowered for word in SKIP_ENVIRONMENT_WORDS):
        return "environment"
    if any(word in lowered for word in SKIP_INPUT_WORDS):
        return "unexecutable"
    # A skip is never a green result for this qualification run.  Keep the
    # raw reason and classify an unexplained skip as unexecutable for review.
    return "unexecutable"


def _testcase_status(testcase: ET.Element) -> tuple[str, str]:
    failure = testcase.find("failure")
    if failure is not None:
        detail = failure.text or failure.attrib.get("message", "")
        normalized = detail.lower().replace("\\\\", "/").replace("\\", "/")
        if "filenotfounderror" in normalized and any(
            marker in normalized for marker in ("/work/phase9_gemini/", "/work/phase11/", "/work/phase14_gemini/")
        ):
            return "unexecutable", detail[-4000:]
        artifact_failure = ("model_artifact_write_failed" in normalized
                            or "model artifact could not be written" in normalized)
        windows_paths = re.findall(r"[a-z]:/[^'\"\n]+", normalized)
        if (artifact_failure and "filenotfounderror" in normalized
                and any(len(path) >= 248 for path in windows_paths)):
            return "environment", detail[-4000:]
        return "failed", detail[-4000:]
    error = testcase.find("error")
    if error is not None:
        detail = (error.text or error.attrib.get("message", ""))[-4000:]
        lowered = detail.lower()
        if any(word in lowered for word in ("modulenotfounderror", "importerror", "plugin", "not installed")):
            return "environment", detail
        if any(word in lowered for word in ("filenotfounderror", "no such file", "missing input", "unavailable")):
            return "unexecutable", detail
        return "failed", detail
    skipped = testcase.find("skipped")
    if skipped is not None:
        message = skipped.attrib.get("message", "") or (skipped.text or "")
        return _skip_status(message), message[-4000:]
    return "passed", ""


def _parse_batches(batch_results: list[BatchResult], nodes: list[str]) -> dict[str, dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = {}
    for batch in batch_results:
        if batch.xml_path.exists():
            try:
                root = ET.parse(batch.xml_path).getroot()
            except ET.ParseError as error:
                root = None
                parse_error = f"JUnit parse error: {error}"
            else:
                parse_error = ""
            if root is not None:
                for testcase in root.iter("testcase"):
                    classname = testcase.attrib.get("classname", "")
                    name = testcase.attrib.get("name", "")
                    nodeid = f"{classname}::{name}" if classname else name
                    status, detail = _testcase_status(testcase)
                    observed[nodeid] = {
                        "status": status,
                        "raw_pytest_status": "failed" if testcase.find("failure") is not None else
                            "error" if testcase.find("error") is not None else
                            "skipped" if testcase.find("skipped") is not None else "passed",
                        "detail": detail,
                        "batch": batch.index,
                        "duration_seconds": float(testcase.attrib.get("time", "0") or 0),
                    }
            else:
                for node in batch.requested:
                    observed[node] = {
                        "status": "environment",
                        "detail": parse_error,
                        "batch": batch.index,
                    }

        requested_set = set(batch.requested)
        missing = requested_set.difference(observed)
        lowered = batch.output.lower()
        if missing:
            if "not found:" in lowered or "no match in any" in lowered or "no tests ran" in lowered:
                status = "unexecutable"
            elif batch.returncode != 0:
                status = "environment"
            else:
                status = "notrun"
            for node in missing:
                observed[node] = {
                    "status": status,
                    "detail": "requested node absent from JUnit report; see batch log",
                    "batch": batch.index,
                }

    for node in nodes:
        observed.setdefault(
            node,
            {
                "status": "notrun",
                "detail": "requested node was not assigned to a batch",
                "batch": None,
            },
        )
    return {node: observed[node] for node in nodes}


def _combine_xml(label: str, batch_results: list[BatchResult], destination: Path) -> None:
    root = ET.Element("testsuites", {"name": f"evaluation-qualification-{label}"})
    for batch in batch_results:
        if not batch.xml_path.exists():
            suite = ET.SubElement(root, "testsuite", {"name": f"batch-{batch.index:02d}", "errors": "1"})
            ET.SubElement(suite, "error", {"message": "JUnit report was not produced"})
            continue
        try:
            parsed = ET.parse(batch.xml_path).getroot()
        except ET.ParseError as error:
            suite = ET.SubElement(root, "testsuite", {"name": f"batch-{batch.index:02d}", "errors": "1"})
            ET.SubElement(suite, "error", {"message": f"JUnit parse error: {error}"})
            continue
        suites = list(parsed.findall("testsuite")) if parsed.tag == "testsuites" else [parsed]
        for suite in suites:
            copied = deepcopy(suite)
            copied.attrib["name"] = f"{label}-batch-{batch.index:02d}"
            root.append(copied)
    ET.indent(root, space="  ")
    destination.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def _classify(baseline: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]], nodes: list[str]) -> dict[str, Any]:
    classifications: dict[str, str] = {}
    for node in nodes:
        before = baseline[node]["status"]
        after = candidate[node]["status"]
        if "notrun" in {before, after}:
            category = "notrun"
        elif "environment" in {before, after}:
            category = "environment"
        elif "unexecutable" in {before, after}:
            category = "unexecutable"
        elif before == "passed" and after == "passed":
            category = "stable_pass"
        elif before == "passed" and after == "failed":
            category = "new_failure"
        elif before == "failed" and after == "passed":
            category = "fixed"
        elif before == "failed" and after == "failed":
            category = "original_failure"
        else:
            category = "notrun"
        classifications[node] = category
    summary = Counter(classifications.values())
    return {
        "summary": dict(sorted(summary.items())),
        "nodes": classifications,
        "status_pairs": {
            f"{before}/{after}": count
            for (before, after), count in sorted(
                Counter((baseline[node]["status"], candidate[node]["status"]) for node in nodes).items()
            )
        },
    }


def _environment_snapshot() -> dict[str, Any]:
    packages: dict[str, Any] = {}
    for name in ("pytest", "pytest-asyncio", "rank-bm25", "PyYAML"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    try:
        import rank_bm25  # type: ignore

        bm25_path = str(Path(rank_bm25.__file__).resolve())
    except Exception as error:  # pragma: no cover - only reports environment
        bm25_path = f"unavailable: {error}"
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version,
        "platform": platform.platform(),
        "pytest_packages": packages,
        "bm25": {"package": "rank-bm25", "path": bm25_path, "version": packages["rank-bm25"]},
        "git": subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout.strip(),
        "provider_budget": 0,
        "provider_calls_allowed": 0,
        "provider_credentials_in_child": False,
        "execution": "serial",
        "retrieval_mode": "bm25",
        "external_transport": "denied by evaluation_offline_plugin",
        "plugin_autoload": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    nodes, fixture = _load_nodes(args.node_file)
    work_dir = args.work_dir.resolve()
    run_id = args.run_id or datetime.now(UTC).strftime("full249-%Y%m%dT%H%M%SZ")
    run_dir = work_dir / run_id
    if run_dir.exists():
        raise ValueError(f"refusing to reuse existing run directory: {run_dir}")
    run_dir.mkdir(parents=True)

    baseline_archive = _archive_commit("baseline", args.baseline_commit, work_dir / "archives", args.checkout_dir)
    candidate_archive = _archive_commit("candidate", args.candidate_commit, work_dir / "archives", args.checkout_dir)
    baseline_batches = _run_side(baseline_archive, nodes, "baseline", run_dir)
    candidate_batches = _run_side(candidate_archive, nodes, "candidate", run_dir)
    baseline_outcomes = _parse_batches(baseline_batches, nodes)
    candidate_outcomes = _parse_batches(candidate_batches, nodes)
    baseline_xml = run_dir / "baseline.xml"
    candidate_xml = run_dir / "candidate.xml"
    _combine_xml("baseline", baseline_batches, baseline_xml)
    _combine_xml("candidate", candidate_batches, candidate_xml)
    classification = _classify(baseline_outcomes, candidate_outcomes, nodes)

    historical = fixture.get("historical_provenance", {})
    result = {
        "schema_version": "evaluation-qualification/core-regression/v1",
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "node_file": str(args.node_file.resolve().relative_to(REPO)),
            "node_file_sha256": _sha256(args.node_file),
            "node_count": len(nodes),
            "source_regression": fixture.get("source"),
        },
        "commits": {
            "baseline": args.baseline_commit,
            "candidate": args.candidate_commit,
        },
        "archives": {
            "baseline": {"root": str(baseline_archive.root), "zip": str(baseline_archive.zip_path), "zip_sha256": baseline_archive.zip_sha256},
            "candidate": {"root": str(candidate_archive.root), "zip": str(candidate_archive.zip_path), "zip_sha256": candidate_archive.zip_sha256},
        },
        "archive_source_verification": {
            "baseline": _verify_archive_sources(baseline_archive.root, baseline_archive.zip_path),
            "candidate": _verify_archive_sources(candidate_archive.root, candidate_archive.zip_path),
        },
        "changed_files": subprocess.run(
            ["git", "diff", "--name-status", args.baseline_commit, args.candidate_commit],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.splitlines(),
        "environment": _environment_snapshot(),
        "execution": {
            "batch_limit_chars": MAX_BATCH_CHARS,
            "baseline_batches": len(baseline_batches),
            "candidate_batches": len(candidate_batches),
            "baseline_returncodes": [batch.returncode for batch in baseline_batches],
            "candidate_returncodes": [batch.returncode for batch in candidate_batches],
            "reports": {"baseline_xml": str(baseline_xml), "candidate_xml": str(candidate_xml)},
            "logs": {
                "baseline": [str(batch.log_path) for batch in baseline_batches],
                "candidate": [str(batch.log_path) for batch in candidate_batches],
            },
        },
        "historical_provenance": {
            "trusted": bool(historical.get("trusted", False)),
            "missing_case_count": int(historical.get("missing_case_count", 38)),
            "available_case_ids": historical.get("available_case_ids", []),
            "note": historical.get(
                "note",
                "No per-node historical XML/log artifact was found in reachable Git; aggregate metadata is retained without fabrication.",
            ),
        },
        "classification": classification,
        "core_suite_passed": classification["summary"].get("stable_pass", 0) == len(nodes),
        "outcomes": {"baseline": baseline_outcomes, "candidate": candidate_outcomes},
    }
    output = run_dir / "classification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["output"] = str(output)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="unique output directory name; defaults to UTC timestamp")
    parser.add_argument("--baseline-commit", default=DEFAULT_BASELINE)
    parser.add_argument("--candidate-commit", default=DEFAULT_CANDIDATE)
    parser.add_argument("--node-file", type=Path, default=DEFAULT_NODE_FILE)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--summarize-run", type=Path,
                        help="reclassify existing raw JUnit evidence without executing tests")
    parser.add_argument("--summary-output", type=Path,
                        help="new output file for --summarize-run; never overwrites raw results")
    parser.add_argument("--checkout-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / ("llc-" + uuid4().hex[:8]),
                        help="short isolated Git-archive path for Windows artifact limits")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.summarize_run:
        if not args.summary_output or args.summary_output.exists():
            parser.error("--summarize-run requires a new --summary-output path")
        result = json.loads((args.summarize_run / "classification.json").read_text(encoding="utf-8"))
        nodes = list(result["outcomes"]["baseline"])
        for side in ("baseline", "candidate"):
            xml = Path(result["execution"]["reports"][side + "_xml"])
            parsed = {}
            for case in ET.parse(xml).getroot().iter("testcase"):
                key = case.attrib["classname"] + "::" + case.attrib["name"]
                status, detail = _testcase_status(case)
                raw = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
                parsed[key] = {**result["outcomes"][side].get(key, {}), "status": status,
                               "raw_pytest_status": raw, "detail": detail}
            result["outcomes"][side] = {n: parsed.get(n, {"status": "notrun", "detail": "absent from raw JUnit"}) for n in nodes}
        result["raw_execution_classification"] = result["classification"]
        result["classification"] = _classify(result["outcomes"]["baseline"], result["outcomes"]["candidate"], nodes)
        result["core_suite_passed"] = result["classification"]["summary"].get("stable_pass", 0) == len(nodes)
        result["normalization"] = {"source_run": str(args.summarize_run), "test_execution": False,
                                   "rule": "Known missing phase9/11/14 artifacts are unexecutable; explicit overlong Windows artifact paths are environment; unproven errors remain failures; raw pytest status retained"}
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result["classification"]["summary"], ensure_ascii=False))
        return 0
    args.node_file = args.node_file.resolve()
    if not args.node_file.is_file():
        raise SystemExit(f"node fixture not found: {args.node_file}")
    result = run(args)
    print(json.dumps({"run_id": result["run_id"], "classification": result["classification"]["summary"], "output": result["output"]}, ensure_ascii=False, indent=2))
    return 0 if result["core_suite_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
