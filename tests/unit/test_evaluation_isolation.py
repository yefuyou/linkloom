import os
import pytest
from linkloom.evaluation.isolation import check_inference_payload, check_inference_module

def test_payload_isolation():
    safe_payload = {"output": "hello", "refs": ["abc"]}
    unsafe_payload_1 = {"expected_output": "hello"}
    unsafe_payload_2 = {"path": "/etc/passwd"}
    
    assert check_inference_payload(safe_payload).is_safe
    assert not check_inference_payload(unsafe_payload_1).is_safe
    assert not check_inference_payload(unsafe_payload_2).is_safe

def test_module_isolation(tmp_path):
    bad_py = tmp_path / "bad.py"
    bad_py.write_text("import relation_eval\nopen('test.txt', 'w')", encoding='utf-8')
    
    report = check_inference_module(str(bad_py))
    assert not report.is_safe
    assert any("Forbidden import" in v for v in report.violations)
    assert any("Unauthorized file write" in v for v in report.violations)
