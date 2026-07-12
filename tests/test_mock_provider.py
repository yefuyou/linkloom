import pytest
from pathlib import Path
from relation_eval.dataset.loader import DatasetLoader
from relation_eval.providers.mock import MockProvider
from relation_eval.schemas import DocumentInput

PROJECT_ROOT = Path("d:/webproject/vault-steward")
FIXTURE_PATH = PROJECT_ROOT / "tests/eval/relation_gold.yaml"
DOCS_ROOT = PROJECT_ROOT / "tests/fixtures/relation_vault"

@pytest.fixture
def gold_dataset():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    return loader.load_gold_dataset()

@pytest.fixture
def documents():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    return loader.load_documents()

def test_perfect_mock(gold_dataset, documents):
    provider = MockProvider(gold_dataset, scenario="perfect")
    
    # Check topic predictions match gold exactly
    for doc in documents:
        pred = provider.predict_topic(doc)
        assert pred.note_path == doc.note_path
        gold_note = next(n for n in gold_dataset.notes if n.note_path == doc.note_path)
        assert pred.should_link_to_agent_evaluation == gold_note.should_link_to_agent_evaluation

    # Check relation predictions match gold
    doc_map = {d.note_path: d for d in documents}
    for p in gold_dataset.expected_pairs:
        left = doc_map[p.source]
        right = doc_map[p.target]
        pred = provider.predict_relation(left, right)
        assert pred.should_link == p.should_link
        if p.should_link:
            assert pred.relation_type == p.relation_type

def test_noisy_mock(gold_dataset, documents):
    provider = MockProvider(gold_dataset, scenario="noisy")
    doc_map = {d.note_path: d for d in documents}

    # Topic FP
    fp_doc = doc_map["tests/fixtures/relation_vault/0420-工作日志.md"]
    pred_fp = provider.predict_topic(fp_doc)
    assert pred_fp.should_link_to_agent_evaluation is True

    # Topic FN
    fn_doc = doc_map["tests/fixtures/relation_vault/灵感-关于状态丢失.md"]
    pred_fn = provider.predict_topic(fn_doc)
    assert pred_fn.should_link_to_agent_evaluation is False

    # Relation FP
    left_fp = doc_map["tests/fixtures/relation_vault/本周流水账-0418.md"]
    right_fp = doc_map["tests/fixtures/relation_vault/0420-工作日志.md"]
    pred_rel_fp = provider.predict_relation(left_fp, right_fp)
    assert pred_rel_fp.should_link is True

    # Relation FN
    left_fn = doc_map["tests/fixtures/relation_vault/0112-随笔.md"]
    right_fn = doc_map["tests/fixtures/relation_vault/Q1阶段性复盘.md"]
    pred_rel_fn = provider.predict_relation(left_fn, right_fn)
    assert pred_rel_fn.should_link is False

def test_malformed_mock(gold_dataset, documents):
    provider = MockProvider(gold_dataset, scenario="malformed")
    doc_map = {d.note_path: d for d in documents}

    # Should fail pydantic parsing due to invalid confidence value 9.9
    from pydantic import ValidationError
    
    # We validate that the return structure actually raises validation errors if parsed
    # Wait, in the mock provider, we instantiate TopicPrediction directly which would fail
    # validation. Let's make sure it raises ValidationError during instantiation.
    with pytest.raises(ValidationError):
        provider.predict_topic(doc_map["tests/fixtures/relation_vault/0112-随笔.md"])

    with pytest.raises(ValidationError):
        provider.predict_topic(doc_map["tests/fixtures/relation_vault/指标体系梳理.md"])

    with pytest.raises(ValidationError):
        provider.predict_relation(
            doc_map["tests/fixtures/relation_vault/Q1阶段性复盘.md"],
            doc_map["tests/fixtures/relation_vault/下半年部门会议.md"]
        )
