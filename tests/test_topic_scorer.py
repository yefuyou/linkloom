import pytest
from pathlib import Path
from relation_eval.dataset.loader import DatasetLoader
from relation_eval.evaluation.topic_scorer import TopicScorer
from relation_eval.schemas import TopicPrediction

PROJECT_ROOT = Path("d:/webproject/vault-steward")
FIXTURE_PATH = PROJECT_ROOT / "tests/eval/relation_gold.yaml"
DOCS_ROOT = PROJECT_ROOT / "tests/fixtures/relation_vault"

@pytest.fixture
def gold_dataset():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    return loader.load_gold_dataset()

def test_topic_scorer_perfect(gold_dataset):
    scorer = TopicScorer(gold_dataset)

    # Generate perfect predictions
    preds = []
    for note in gold_dataset.notes:
        preds.append(TopicPrediction(
            note_path=note.note_path,
            predicted_topic_ids=["agent-evaluation"] if note.should_link_to_agent_evaluation else [],
            should_link_to_agent_evaluation=note.should_link_to_agent_evaluation,
            confidence=1.0,
            evidence=note.evidence,
            reason="perfect"
        ))

    results = scorer.score(preds)
    metrics = results["metrics"]
    counts = results["counts"]

    assert metrics["accuracy"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["false_negative_rate"] == 0.0
    assert counts["true_positive"] == 7
    assert counts["true_negative"] == 3
    assert counts["false_positive"] == 0
    assert counts["false_negative"] == 0

def test_topic_scorer_noisy(gold_dataset):
    scorer = TopicScorer(gold_dataset)

    # Introduce 1 FP and 1 FN
    # Gold positive count: 7, Gold negative count: 3
    preds = []
    for note in gold_dataset.notes:
        pred_label = note.should_link_to_agent_evaluation
        if note.note_path == "tests/fixtures/relation_vault/0420-工作日志.md":
            # Gold: False -> Pred: True (FP)
            pred_label = True
        elif note.note_path == "tests/fixtures/relation_vault/灵感-关于状态丢失.md":
            # Gold: True -> Pred: False (FN)
            pred_label = False

        preds.append(TopicPrediction(
            note_path=note.note_path,
            predicted_topic_ids=["agent-evaluation"] if pred_label else [],
            should_link_to_agent_evaluation=pred_label,
            confidence=0.9,
            evidence=[],
            reason="noisy"
        ))

    results = scorer.score(preds)
    metrics = results["metrics"]
    counts = results["counts"]

    # Gold positives: 7 (but 1 FN, so TP=6, FN=1)
    # Gold negatives: 3 (but 1 FP, so TN=2, FP=1)
    assert counts["true_positive"] == 6
    assert counts["true_negative"] == 2
    assert counts["false_positive"] == 1
    assert counts["false_negative"] == 1

    # Accuracy: (6+2)/10 = 0.8
    assert metrics["accuracy"] == 0.8
    # Precision: 6 / (6+1) = 6/7
    assert abs(metrics["precision"] - 6/7) < 1e-5
    # Recall: 6 / (6+1) = 6/7
    assert abs(metrics["recall"] - 6/7) < 1e-5
    # FPR: 1 / (1+2) = 1/3
    assert abs(metrics["false_positive_rate"] - 1/3) < 1e-5
    # FNR: 1 / (1+6) = 1/7
    assert abs(metrics["false_negative_rate"] - 1/7) < 1e-5
