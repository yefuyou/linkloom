import pytest
from linkloom.memory.policy import validate_candidate_value, MemoryPolicyViolation

def test_reject_gold_label_expected():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"Gold": "value"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"expected_result": 42})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"my_label": "positive"})

def test_reject_raw_source_bodies():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"body": "some text"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"raw_text": "text"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"content_html": "<html>"})

def test_reject_absolute_paths():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"path": "/usr/bin/local"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"path": "C:\\Windows\\System32"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"path": "\\\\network\\share"})

def test_reject_secrets():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"token": "sk-12345"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"auth": "Bearer abcdef"})
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"info": "my password is secret"})

def test_reject_long_text():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"summary": "A" * 1001})

def test_valid_value():
    validate_candidate_value({
        "summary": "This is a brief summary.",
        "tags": ["architecture", "memory"],
        "count": 5
    })


def test_reject_nested_forbidden_metadata_and_source_refs():
    with pytest.raises(MemoryPolicyViolation):
        validate_candidate_value({"nested": {"ground_truth": "yes"}})
    with pytest.raises(MemoryPolicyViolation):
        from linkloom.memory.policy import validate_memory_input
        validate_memory_input("preference", {"summary": "brief"}, ["/absolute/ref"], {"owner": "human"})
    with pytest.raises(MemoryPolicyViolation):
        from linkloom.memory.policy import validate_memory_input
        validate_memory_input("preference", {"summary": "brief"}, ["ev_1"], {"expected": "no"})
