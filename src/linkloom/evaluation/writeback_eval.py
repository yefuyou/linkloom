import json
import traceback
from pathlib import Path
from typing import Dict, Any, List
import uuid

from linkloom.mutations.e2e import E2EWritebackRunner
from linkloom.mutations.apply import apply_plan

class WritebackEvaluator:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.metrics = {
            "cases_total": 8,
            "cases_passed": 0,
            "reject_safe": False,
            "stale_safe": False,
            "traversal_safe": False,
            "duplicate_safe": False,
            "terminal_resume_safe": False,
            "writer_failure_rolled_back": False,
            "verification_failure_rolled_back": False,
            "unauthorized_writes": 0,
            "trace_contiguous": True,
            "redaction_passed": True,
            "all_non_success_audited": True,
            "passed": False,
            "failures": []
        }
        self.results = []
        
    def _scan_for_redactions(self, directory: Path):
        forbidden = [
            str(Path.cwd()),
            "# Note 1",
            "Content of note 1.",
            "Additional line.",
            "\"preference\": \"test\"",
            "Test query",
            "diff --git",
            "@@ -",
            "--- a/",
            "+++ b/",
            "Gold",
            "expected_pairs",
            "ground_truth",
            "# Note 1 leak"
        ]
        
        for p in directory.rglob("*"):
            if p.is_file() and "target" not in p.parts and "backup" not in p.parts:
                try:
                    content = p.read_text("utf-8")
                    for f in forbidden:
                        if f in content:
                            self.metrics["redaction_passed"] = False
                except UnicodeDecodeError:
                    pass

    def run_failure_matrix(self):
        cases = [
            ("success", "APPROVE_DEFAULT"),
            ("reject", "REJECT_DEFAULT"),
            ("stale", "APPROVE_DEFAULT"),
            ("traversal", "APPROVE_DEFAULT"),
            ("duplicate", "APPROVE_DEFAULT"),
            ("terminal", "APPROVE_DEFAULT"),
            ("writer_failure", "APPROVE_DEFAULT"),
            ("verification_failure", "APPROVE_DEFAULT")
        ]

        unauthorized_writes_count = 0

        for case_name, approval_text in cases:
            run_output = self.output_dir / f"case_{case_name}"
            
            import tempfile
            with tempfile.TemporaryDirectory(prefix="linkloom_eval_") as tmp_dir_str:
                tmp_dir = Path(tmp_dir_str)
                target_root = tmp_dir / "target"
                backup_root = tmp_dir / "backup"
                runner = E2EWritebackRunner(run_output)
            
                result_obj = {
                    "name": case_name,
                    "passed": False,
                    "status": None,
                    "error_code": None,
                    "before_sha256": None,
                    "after_sha256": None,
                    "apply_calls": 0,
                    "writer_calls": 0,
                    "verifier_calls": 0,
                    "checks": []
                }
                
                try:
                    manifest = runner.run_e2e(target_root, backup_root, approval_text, scenario=case_name)
                    status = manifest.get("status")
                    error_code = manifest.get("error_code")
                    sc = manifest.get("scenario_checks", {})
                    
                    result_obj["status"] = status
                    result_obj["error_code"] = error_code
                    result_obj["before_sha256"] = sc.get("before_sha256")
                    result_obj["after_sha256"] = sc.get("after_sha256")
                    result_obj["apply_calls"] = sc.get("apply_calls", 0)
                    result_obj["writer_calls"] = sc.get("writer_calls", 0)
                    result_obj["verifier_calls"] = sc.get("verifier_calls", 0)
                    
                    b_sha = result_obj["before_sha256"]
                    a_sha = result_obj["after_sha256"]
                    ap_calls = result_obj["apply_calls"]
                    w_calls = result_obj["writer_calls"]
                    
                    passed = False
                    
                    # Trace audit
                    events_path = run_output / manifest.get("trace_artifact_ref", "")
                    if events_path.exists():
                        events = [json.loads(line) for line in events_path.read_text().strip().split("\n") if line.strip()]
                        seqs = [e.get("seq", -1) for e in events]
                        if seqs != list(range(1, len(seqs) + 1)):
                            self.metrics["trace_contiguous"] = False
                            
                        if case_name != "success":
                            types = [e.get("event_type", e.get("type")) for e in events]
                            if not any(t in ["writeback.approval.rejected", "writeback.failed", "writeback.rollback"] for t in types):
                                self.metrics["all_non_success_audited"] = False
                    else:
                        self.metrics["trace_contiguous"] = False
                        if case_name != "success":
                            self.metrics["all_non_success_audited"] = False
    
                    if case_name == "success":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "applied" and ap_calls == 1 and w_calls == 1 and b_sha != a_sha:
                            passed = True
                    elif case_name == "reject":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "rejected" and ap_calls == 0 and w_calls == 0 and b_sha == a_sha:
                            self.metrics["reject_safe"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "stale":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"stale={'STALE' in str(error_code).upper()}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status in ("failed", "stale") and "STALE" in str(error_code).upper() and ap_calls == 0 and w_calls == 0 and b_sha == a_sha:
                            self.metrics["stale_safe"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "traversal":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"traversal={'TRAVERSAL' in str(error_code).upper() or 'NOT_ALLOWED' in str(error_code).upper() or 'PERMISSIONERROR' in str(error_code).upper()}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "failed" and ("TRAVERSAL" in str(error_code).upper() or "NOT_ALLOWED" in str(error_code).upper() or "PERMISSIONERROR" in str(error_code).upper()) and ap_calls == 0 and w_calls == 0 and b_sha == a_sha:
                            self.metrics["traversal_safe"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "duplicate":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"dup_err={sc.get('duplicate_error')}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "applied" and sc.get("duplicate_error") == "DUPLICATE_APPROVAL" and ap_calls == 1 and w_calls == 1 and b_sha != a_sha:
                            self.metrics["duplicate_safe"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "terminal":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"term_err={sc.get('terminal_error')}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "applied" and sc.get("terminal_error") == "RESUME_TERMINAL" and ap_calls == 1 and w_calls == 1 and b_sha != a_sha:
                            self.metrics["terminal_resume_safe"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "writer_failure":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "rolled_back" and ap_calls == 1 and w_calls >= 1 and b_sha == a_sha:
                            self.metrics["writer_failure_rolled_back"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                    elif case_name == "verification_failure":
                        result_obj["checks"].append(f"status={status}")
                        result_obj["checks"].append(f"ap_calls={ap_calls}")
                        result_obj["checks"].append(f"w_calls={w_calls}")
                        result_obj["checks"].append(f"changed={b_sha != a_sha}")
                        if status == "rolled_back" and ap_calls == 1 and w_calls >= 1 and b_sha == a_sha:
                            self.metrics["verification_failure_rolled_back"] = True
                            passed = True
                        else:
                            unauthorized_writes_count += 1
                            
                    result_obj["passed"] = passed
                    self.results.append(result_obj)
                    
                except Exception as e:
                    result_obj["error_code"] = type(e).__name__
                    self.results.append(result_obj)
                    pass
                
        self.metrics["unauthorized_writes"] = unauthorized_writes_count
        
        passed_cases = sum(1 for r in self.results if r["passed"])
        self.metrics["cases_passed"] = passed_cases
        
        self.metrics["passed"] = (
            passed_cases == 8 and
            self.metrics["unauthorized_writes"] == 0 and
            self.metrics["trace_contiguous"] and
            self.metrics["redaction_passed"] and
            self.metrics["all_non_success_audited"]
        )
        
        # Collect failures
        for r in self.results:
            if not r["passed"]:
                print(f"FAILED CASE: {r['name']}, error: {r['error_code']}, status: {r['status']}, checks: {r['checks']}")
                self.metrics["failures"].append(r)
        
        # Write matrix output
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "writeback_metrics.json").write_text(json.dumps(self.metrics, indent=2))
        (self.output_dir / "failure_matrix.json").write_text(json.dumps(self.results, indent=2))
            
        report = f"# Writeback E2E Report\nPassed: {self.metrics['passed']}\nCases: {passed_cases}/8\n"
        (self.output_dir / "report.md").write_text(report)
        
        # Run redaction scan on everything including the output files
        self._scan_for_redactions(self.output_dir)
        
        # Re-evaluate passed and rewrite metrics if redaction failed
        if not self.metrics["redaction_passed"]:
            self.metrics["passed"] = False
            
        # Negative sentinel test
        sentinel = self.output_dir / "traces" / "fake.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("Test query leak", encoding="utf-8")
        
        clean_redaction_passed = self.metrics["redaction_passed"]
        
        self.metrics["redaction_passed"] = True
        self._scan_for_redactions(self.output_dir)
        negative_redaction_passed = self.metrics["redaction_passed"]
        
        self.metrics["redaction_passed"] = clean_redaction_passed
        if negative_redaction_passed:
            self.metrics["passed"] = False
            
        if sentinel.exists():
            sentinel.unlink()
            
        (self.output_dir / "writeback_metrics.json").write_text(json.dumps(self.metrics, indent=2))
        report = f"# Writeback E2E Report\nPassed: {self.metrics['passed']}\nCases: {passed_cases}/8\n"
        (self.output_dir / "report.md").write_text(report)
            
            
        return self.metrics

def run_evaluation(output_dir: Path):
    evaluator = WritebackEvaluator(output_dir)
    return evaluator.run_failure_matrix()
