"""Run the offline qualification Core Gate from a checked-in pytest manifest.

Each target is executed as its own pytest process and receives a dedicated
JUnit file.  A target passes only when every testcase is green; skipped,
failed, errored, empty, or uncollected targets fail closed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4
import xml.etree.ElementTree as ET
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NODE_MARKERS = ("smoke", "real_provider", "live_provider", "external_network")
SECRET_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_API_BASE", "GOOGLE_API_KEY", "GEMINI_API_KEY",
                   "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def _node_path(node: str) -> str:
    return node.split("::", 1)[0].split("[", 1)[0]


def _junit_cases(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testcase":
        return [root]
    return list(root.iter("testcase"))


def parse_junit_result(path: Path | str) -> dict[str, Any]:
    """Normalize one JUnit file; skipped/error never become pass."""
    path = Path(path)
    if not path.exists():
        return {"status": "error", "tests": 0, "passed": 0, "failed": 0, "skipped": 0,
                "errors": 1, "message": "JUnit file was not produced"}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        return {"status": "error", "tests": 0, "passed": 0, "failed": 0, "skipped": 0,
                "errors": 1, "message": str(error)}
    cases = _junit_cases(root)
    failed = sum(1 for case in cases if case.find("failure") is not None)
    errors = sum(1 for case in cases if case.find("error") is not None)
    skipped = sum(1 for case in cases if case.find("skipped") is not None)
    passed = len(cases) - failed - errors - skipped
    status = "passed" if cases and not failed and not errors and not skipped else "failed"
    return {"status": status, "tests": len(cases), "passed": passed, "failed": failed,
            "skipped": skipped, "errors": errors,
            "names": [f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}" for case in cases]}


def load_manifest(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != "evaluation-core-gate/v1":
        raise ValueError("unsupported Core Gate manifest schema")
    if not isinstance(data.get("targets"), list) or not data["targets"]:
        raise ValueError("Core Gate manifest requires non-empty targets")
    return data


def validate_manifest(manifest: dict[str, Any], root: Path | str = ROOT, *, require_tracked: bool = True) -> None:
    root = Path(root).resolve()
    seen: set[str] = set()
    dependencies = manifest.get("tracked_dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(path, str) for path in dependencies):
        raise ValueError("tracked_dependencies must be a list of relative paths")
    paths_to_check = list(dependencies)
    for target in manifest["targets"]:
        if not isinstance(target, dict) or not isinstance(target.get("id"), str):
            raise ValueError("each Core Gate target requires an id")
        identifier = target["id"]
        if identifier in seen:
            raise ValueError(f"duplicate Core Gate target: {identifier}")
        seen.add(identifier)
        nodes = target.get("pytest_nodes")
        if type(target.get("expected_tests")) is not int or target["expected_tests"] < 1:
            raise ValueError(f"target {identifier} must declare its expected collected case count")
        if not isinstance(nodes, list) or not nodes or any(not isinstance(node, str) or not node.strip() for node in nodes):
            raise ValueError(f"target {identifier} has no pytest nodes")
        for node in nodes:
            if any(marker in node.casefold() for marker in FORBIDDEN_NODE_MARKERS):
                raise ValueError(f"external/provider test is forbidden in offline Core Gate: {node}")
            paths_to_check.append(_node_path(node))
    for relative in paths_to_check:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"manifest path must stay within repository: {relative}")
        absolute = root / path
        if not absolute.is_file():
            raise ValueError(f"manifest dependency is missing: {relative}")
        if require_tracked:
            checked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", relative], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if checked.returncode != 0:
                raise ValueError(f"manifest dependency is not tracked: {relative}")


def _offline_environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    pythonpath = [str(root), str(root / "scripts"), str(root / "src"), str(root / "tests/integration")]
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    environment["LINKLOOM_EVALUATION_OFFLINE"] = "1"
    environment["LINKLOOM_DISABLE_NETWORK"] = "1"
    environment["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    for key in SECRET_ENV_KEYS:
        environment.pop(key, None)
    return environment


def run_gate(manifest: dict[str, Any], *, root: Path | str = ROOT, output: Path | str,
             require_tracked: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    output = Path(output).resolve()
    validate_manifest(manifest, root, require_tracked=require_tracked)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        raise ValueError("refusing to overwrite an existing Core Gate report")
    targets: list[dict[str, Any]] = []
    for target in manifest["targets"]:
        identifier = target["id"]
        junit = output / f"{identifier}.xml"
        # Runtime artifact paths contain run/turn identities. A deeply nested
        # worktree basetemp exceeds Windows MAX_PATH before the intended guard.
        # Keep this unique directory for evidence; pytest alone creates it.
        basetemp = Path(tempfile.gettempdir()) / ("llg-" + uuid4().hex[:8])
        command = [sys.executable, "-m", "pytest", *target["pytest_nodes"], "-q",
                   "-p", "evaluation_offline_plugin", "-p", "no:cacheprovider",
                   "--basetemp", str(basetemp),
                   "--disable-warnings", "--tb=short", "--junitxml", str(junit)]
        completed = subprocess.run(command, cwd=root, env=_offline_environment(root), check=False,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        (output / (identifier + ".log")).write_text(completed.stdout + completed.stderr, encoding="utf-8")
        parsed = parse_junit_result(junit)
        if parsed["tests"] != target["expected_tests"]:
            parsed["status"] = "failed"
            parsed["message"] = f"Expected {target['expected_tests']} collected cases; observed {parsed['tests']}"
        if completed.returncode != 0 and parsed["status"] == "passed":
            parsed["status"] = "failed"
            parsed["message"] = f"pytest exited with {completed.returncode}"
        row = {"id": identifier, "boundary": target.get("boundary", identifier),
               "basetemp": str(basetemp),
               "pytest_nodes": list(target["pytest_nodes"]), "pytest_returncode": completed.returncode,
               "junit": str(junit.relative_to(root)) if junit.is_relative_to(root) else str(junit),
               **parsed}
        observations = []
        for path in sorted(basetemp.rglob("observation.json")):
            observed = json.loads(path.read_text(encoding="utf-8"))
            if observed.get("schema_version") == "qualified-runtime/v1":
                observations.append({"path": str(path), "observation": observed})
        row["runtime_observations"] = observations
        if completed.returncode != 0 and "message" not in row:
            row["message"] = completed.stderr[-2000:] or completed.stdout[-2000:]
        targets.append(row)
    passed = sum(row["status"] == "passed" for row in targets)
    report = {"schema_version": "evaluation-core-gate-report/v1",
              "status": "PASSED" if passed == len(targets) else "FAILED",
              "passed": passed, "total": len(targets), "targets": targets,
              "network": "deny", "provider_sdk_requests": 0}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests" / "fixtures" / "evaluation_qualification" / "core_gate_manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "evaluation-core-gate")
    parser.add_argument("--allow-untracked", action="store_true", help="local development escape hatch before files are staged")
    args = parser.parse_args()
    try:
        report = run_gate(load_manifest(args.manifest), root=ROOT, output=args.output,
                          require_tracked=not args.allow_untracked)
    except Exception as error:
        print(f"Core Gate ERROR: {error}", file=sys.stderr)
        return 2
    print(f"Core Gate {report['status']}: {report['passed']}/{report['total']} targets")
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
