import pytest
from pathlib import Path
from relation_eval.dataset.loader import DatasetLoader
from relation_eval.evaluation.evidence_validator import EvidenceValidator
from relation_eval.schemas import TopicPrediction, RelationPrediction, RelationEvidence

PROJECT_ROOT = Path("d:/webproject/vault-steward")
FIXTURE_PATH = PROJECT_ROOT / "tests/eval/relation_gold.yaml"
DOCS_ROOT = PROJECT_ROOT / "tests/fixtures/relation_vault"

@pytest.fixture
def doc_contents():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    docs = loader.load_documents()
    return {d.note_path: d.content for d in docs}

def test_evidence_validator_topic(doc_contents):
    validator = EvidenceValidator(doc_contents)

    # Valid topic prediction
    path = "tests/fixtures/relation_vault/0112-随笔.md"
    pred_ok = TopicPrediction(
        note_path=path,
        predicted_topic_ids=["agent-evaluation"],
        should_link_to_agent_evaluation=True,
        confidence=1.0,
        evidence=["最终答案碰巧蒙对的情况比想象中多"],
        reason="valid"
    )
    is_ok, issues = validator.validate_topic_prediction(pred_ok)
    assert is_ok is True
    assert len(issues) == 0

    # Invalid evidence (substring not found)
    pred_bad_ev = TopicPrediction(
        note_path=path,
        predicted_topic_ids=["agent-evaluation"],
        should_link_to_agent_evaluation=True,
        confidence=1.0,
        evidence=["this is not in the text"],
        reason="invalid ev"
    )
    is_ok, issues = validator.validate_topic_prediction(pred_bad_ev)
    assert is_ok is False
    assert any(i["type"] == "EVIDENCE_NOT_IN_SOURCE" for i in issues)

    # Missing evidence
    pred_missing = TopicPrediction(
        note_path=path,
        predicted_topic_ids=["agent-evaluation"],
        should_link_to_agent_evaluation=True,
        confidence=1.0,
        evidence=[],
        reason="missing ev"
    )
    is_ok, issues = validator.validate_topic_prediction(pred_missing)
    assert is_ok is False
    assert any(i["type"] == "EVIDENCE_MISSING" for i in issues)

    # Unexpected evidence
    pred_unexpected = TopicPrediction(
        note_path=path,
        predicted_topic_ids=[],
        should_link_to_agent_evaluation=False,
        confidence=1.0,
        evidence=["some ev"],
        reason="unexpected"
    )
    is_ok, issues = validator.validate_topic_prediction(pred_unexpected)
    assert is_ok is False
    assert any(i["type"] == "EVIDENCE_UNEXPECTED" for i in issues)

def test_evidence_validator_relation(doc_contents):
    validator = EvidenceValidator(doc_contents)
    left_path = "tests/fixtures/relation_vault/指标体系梳理.md"
    right_path = "tests/fixtures/relation_vault/面经-第二轮.md"

    # Valid relation prediction
    pred_ok = RelationPrediction(
        left_note_path=left_path,
        right_note_path=right_path,
        should_link=True,
        source=left_path,
        target=right_path,
        relation_type="concept-to-interview",
        confidence=1.0,
        evidence=RelationEvidence(
            source=["一条 case 多个错误怎么归因"],
            target=["检索没找到正确段落就是检索的问题，找到了但抽错就是抽取参数的问题，抽对了但定位校验没拦住就是校验规则的问题"]
        ),
        reason="valid"
    )
    is_ok, issues = validator.validate_relation_prediction(pred_ok)
    assert is_ok is True
    assert len(issues) == 0

    # Invalid source evidence
    pred_bad_src = RelationPrediction(
        left_note_path=left_path,
        right_note_path=right_path,
        should_link=True,
        source=left_path,
        target=right_path,
        relation_type="concept-to-interview",
        confidence=1.0,
        evidence=RelationEvidence(
            source=["this is not in source"],
            target=["检索没找到正确段落就是检索的问题，找到了但抽错就是抽取参数的问题，抽对了但定位校验没拦住就是校验规则的问题"]
        ),
        reason="invalid source ev"
    )
    is_ok, issues = validator.validate_relation_prediction(pred_bad_src)
    assert is_ok is False
    assert any(i["type"] == "EVIDENCE_NOT_IN_SOURCE" for i in issues)

def test_validate_gold_dataset(doc_contents):
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    gold = loader.load_gold_dataset()
    validator = EvidenceValidator(doc_contents)

    issues = validator.validate_gold_dataset(gold)
    
    # Since our gold evidence was updated and verified, there should be 0 issues in the gold dataset!
    # Let's assert it is empty.
    assert len(issues) == 0
