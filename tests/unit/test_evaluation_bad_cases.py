from linkloom.evaluation.bad_cases import create_bad_case, BadCaseCategory, BadCaseSeverity

def test_bad_case_creation():
    bc = create_bad_case("A", "B", BadCaseCategory.FALSE_POSITIVE, BadCaseSeverity.MEDIUM, "test", ["ref1"])
    assert bc.category == "false_positive"
    assert bc.severity == "medium"
    assert bc.reason == "test"
    assert bc.safe_refs == ["ref1"]
