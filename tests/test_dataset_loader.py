import os
from pathlib import Path
from relation_eval.dataset.loader import DatasetLoader

PROJECT_ROOT = Path("d:/webproject/vault-steward")
FIXTURE_PATH = PROJECT_ROOT / "tests/eval/relation_gold.yaml"
DOCS_ROOT = PROJECT_ROOT / "tests/fixtures/relation_vault"

def test_loader_gold_dataset():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    gold = loader.load_gold_dataset()

    assert gold.dataset_version == "relation_vault_v1"
    assert len(gold.notes) == 10

    # Ensure topic ids and linking flags match
    for note in gold.notes:
        if note.should_link_to_agent_evaluation:
            assert "agent-evaluation" in note.expected_topic_ids
        else:
            assert "agent-evaluation" not in note.expected_topic_ids

    # Check normalization of false-positive relation_type
    fp_pairs = [p for p in gold.expected_pairs if p.relation_type == "false-positive"]
    assert len(fp_pairs) == 3
    for p in fp_pairs:
        assert p.should_link is False

    # Positive relation pairs check
    positive_pairs = [p for p in gold.expected_pairs if p.should_link is True]
    assert len(positive_pairs) == 3
    for p in positive_pairs:
        assert p.relation_type != "false-positive"

def test_loader_documents():
    loader = DatasetLoader(FIXTURE_PATH, DOCS_ROOT)
    docs = loader.load_documents()

    assert len(docs) == 10
    paths = [d.note_path for d in docs]
    assert len(paths) == len(set(paths)), "Document paths must be unique"

    for doc in docs:
        assert doc.note_path.startswith("tests/fixtures/relation_vault/")
        assert doc.note_path.endswith(".md")
        assert len(doc.content) > 0
        assert len(doc.content_hash) == 64  # sha256 hex length
