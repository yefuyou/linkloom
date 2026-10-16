"""Differential classification retains failures and unavailable input evidence."""
import xml.etree.ElementTree as ET

import pytest

from scripts.evaluation_core_regression import _classify, _testcase_status


@pytest.mark.parametrize("phase", ["phase9_gemini", "phase11", "phase14_gemini"])
def test_exact_missing_historical_inputs_are_unexecutable(phase):
    case = ET.Element("testcase")
    ET.SubElement(case, "failure").text = (
        f"FileNotFoundError: C:/archive/work/{phase}/runs/original/observed.json")
    assert _testcase_status(case)[0] == "unexecutable"


@pytest.mark.parametrize("detail", [
    "MODEL_ARTIFACT_WRITE_FAILED", "TimeoutError: timed out",
    "FileNotFoundError: C:/new-product-input.json",
])
def test_unproven_errors_are_not_relabelled_environment(detail):
    case = ET.Element("testcase")
    ET.SubElement(case, "failure").text = detail
    assert _testcase_status(case)[0] == "failed"


def test_diff_does_not_promote_unavailable_or_unexecuted_to_pass():
    pairs = [("failed", "failed", "original_failure"), ("passed", "failed", "new_failure"),
             ("failed", "passed", "fixed"), ("passed", "passed", "stable_pass"),
             ("unexecutable", "passed", "unexecutable"), ("notrun", "passed", "notrun"),
             ("environment", "passed", "environment")]
    before = {str(i): {"status": a} for i, (a, _, _) in enumerate(pairs)}
    after = {str(i): {"status": b} for i, (_, b, _) in enumerate(pairs)}
    result = _classify(before, after, list(before))
    assert result["nodes"] == {str(i): expected for i, (_, _, expected) in enumerate(pairs)}
