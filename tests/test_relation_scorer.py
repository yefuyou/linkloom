import pytest
from pathlib import Path
from relation_eval.dataset.loader import DatasetLoader
from relation_eval.evaluation.relation_scorer import RelationScorer
from relation_eval.schemas import RelationPrediction, RelationEvidence

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

def test_relation_scorer_perfect(gold_dataset, documents):
    scorer = RelationScorer(gold_dataset)
    
    # Unordered lookup for expected pairs
    gold_map = {}
    for p in gold_dataset.expected_pairs:
        key = tuple(sorted([p.source, p.target]))
        gold_map[key] = p

    # Generate perfect predictions for all 45 combinations
    preds = []
    for i in range(len(documents)):
        for j in range(i + 1, len(documents)):
            left = documents[i]
            right = documents[j]
            key = tuple(sorted([left.note_path, right.note_path]))
            gold = gold_map.get(key)

            if gold and gold.should_link:
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=True,
                    source=gold.source,
                    target=gold.target,
                    relation_type=gold.relation_type,
                    confidence=1.0,
                    evidence=RelationEvidence(
                        source=gold.evidence.source,
                        target=gold.evidence.target
                    ),
                    reason="perfect"
                )
            else:
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=False,
                    source=None,
                    target=None,
                    relation_type=None,
                    confidence=1.0,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="unrelated"
                )
            preds.append(pred)

    results = scorer.score(preds)
    metrics = results["metrics"]
    counts = results["counts"]

    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["direction_accuracy"] == 1.0
    assert metrics["type_accuracy"] == 1.0
    assert counts["true_positive"] == 3
    assert counts["true_negative"] == 42
    assert counts["false_positive"] == 0
    assert counts["false_negative"] == 0
    assert counts["correct_direction"] == 3
    assert counts["correct_type"] == 3

def test_relation_scorer_noisy(gold_dataset, documents):
    scorer = RelationScorer(gold_dataset)
    
    gold_map = {}
    for p in gold_dataset.expected_pairs:
        key = tuple(sorted([p.source, p.target]))
        gold_map[key] = p

    # Generate predictions with specific error injections:
    # 1 FP: 本周流水账-0418.md & 0420-工作日志.md (unrelated -> linked)
    # 1 FN: 0112-随笔.md & Q1阶段性复盘.md (linked -> unrelated)
    # 1 Type Error: 指标体系梳理.md & 面经-第二轮.md (concept-to-interview -> concept-to-practice)
    # 1 Direction Error: 本周流水账-0418.md & 周二沟通记录.md (swapped source/target)
    preds = []
    for i in range(len(documents)):
        for j in range(i + 1, len(documents)):
            left = documents[i]
            right = documents[j]
            key = tuple(sorted([left.note_path, right.note_path]))
            
            # Match injections
            if (left.note_path == "tests/fixtures/relation_vault/本周流水账-0418.md" and right.note_path == "tests/fixtures/relation_vault/0420-工作日志.md") or \
               (right.note_path == "tests/fixtures/relation_vault/本周流水账-0418.md" and left.note_path == "tests/fixtures/relation_vault/0420-工作日志.md"):
                # FP Injection
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=True,
                    source="tests/fixtures/relation_vault/本周流水账-0418.md",
                    target="tests/fixtures/relation_vault/0420-工作日志.md",
                    relation_type="concept-to-practice",
                    confidence=0.9,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="noisy FP"
                )
            elif (left.note_path == "tests/fixtures/relation_vault/0112-随笔.md" and right.note_path == "tests/fixtures/relation_vault/Q1阶段性复盘.md") or \
                 (right.note_path == "tests/fixtures/relation_vault/0112-随笔.md" and left.note_path == "tests/fixtures/relation_vault/Q1阶段性复盘.md"):
                # FN Injection
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=False,
                    source=None,
                    target=None,
                    relation_type=None,
                    confidence=0.9,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="noisy FN"
                )
            elif (left.note_path == "tests/fixtures/relation_vault/指标体系梳理.md" and right.note_path == "tests/fixtures/relation_vault/面经-第二轮.md") or \
                 (right.note_path == "tests/fixtures/relation_vault/指标体系梳理.md" and left.note_path == "tests/fixtures/relation_vault/面经-第二轮.md"):
                # Type Error Injection (gold is concept-to-interview)
                gold = gold_map[key]
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=True,
                    source=gold.source,
                    target=gold.target,
                    relation_type="concept-to-practice",
                    confidence=0.9,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="noisy Type error"
                )
            elif (left.note_path == "tests/fixtures/relation_vault/本周流水账-0418.md" and right.note_path == "tests/fixtures/relation_vault/周二沟通记录.md") or \
                 (right.note_path == "tests/fixtures/relation_vault/本周流水账-0418.md" and left.note_path == "tests/fixtures/relation_vault/周二沟通记录.md"):
                # Direction Error Injection (gold source=流水账, target=沟通记录)
                gold = gold_map[key]
                pred = RelationPrediction(
                    left_note_path=left.note_path,
                    right_note_path=right.note_path,
                    should_link=True,
                    source=gold.target,  # Swapped
                    target=gold.source,  # Swapped
                    relation_type=gold.relation_type,
                    confidence=0.9,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="noisy Direction error"
                )
            else:
                # Default perfect mapping
                gold = gold_map.get(key)
                if gold and gold.should_link:
                    pred = RelationPrediction(
                        left_note_path=left.note_path,
                        right_note_path=right.note_path,
                        should_link=True,
                        source=gold.source,
                        target=gold.target,
                        relation_type=gold.relation_type,
                        confidence=1.0,
                        evidence=RelationEvidence(
                            source=gold.evidence.source,
                            target=gold.evidence.target
                        ),
                        reason="perfect"
                    )
                else:
                    pred = RelationPrediction(
                        left_note_path=left.note_path,
                        right_note_path=right.note_path,
                        should_link=False,
                        source=None,
                        target=None,
                        relation_type=None,
                        confidence=1.0,
                        evidence=RelationEvidence(source=[], target=[]),
                        reason="unrelated"
                    )
            preds.append(pred)

    results = scorer.score(preds)
    metrics = results["metrics"]
    counts = results["counts"]

    # Gold linkage positive total: 3.
    # Predict linkage positive total: 3 (perfect minus 1 FN = 2, plus 1 FP = 3).
    # TP: 2 (type-error, direction-error), FP: 1 (unrelated pair), FN: 1 (随笔 & 复盘).
    assert counts["true_positive"] == 2
    assert counts["true_negative"] == 41
    assert counts["false_positive"] == 1
    assert counts["false_negative"] == 1

    # Precision: 2 / 3 = 66.67%
    assert abs(metrics["precision"] - 2/3) < 1e-5
    # Recall: 2 / 3 = 66.67%
    assert abs(metrics["recall"] - 2/3) < 1e-5

    # linkage_tps = 2.
    # Direction check on the 2 linkage TPs:
    # 1. 指标体系梳理 & 面经-第二轮: direction correct, type incorrect.
    # 2. 流水账 & 沟通记录: direction incorrect, type correct.
    assert counts["correct_direction"] == 1
    assert counts["correct_type"] == 1

    assert metrics["direction_accuracy"] == 0.5
    assert metrics["type_accuracy"] == 0.5
