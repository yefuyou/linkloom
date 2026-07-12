import os
import yaml
import hashlib
from pathlib import Path
from typing import List, Dict
from relation_eval.dataset.models import GoldDataset, GoldPair, GoldNote
from relation_eval.schemas import DocumentInput

class DatasetLoader:
    def __init__(self, fixture_path: str | Path, documents_root: str | Path):
        self.fixture_path = Path(fixture_path)
        self.documents_root = Path(documents_root)

    def load_gold_dataset(self) -> GoldDataset:
        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Normalize expected_pairs: relation_type == "false-positive" -> should_link = False
        notes_raw = data.get("notes", [])
        pairs_raw = data.get("expected_pairs", [])
        version = data.get("dataset_version", "unknown")

        notes = [GoldNote(**n) for n in notes_raw]
        expected_pairs = []
        for p in pairs_raw:
            pair = GoldPair(**p)
            if pair.relation_type == "false-positive":
                pair.should_link = False
            expected_pairs.append(pair)

        return GoldDataset(
            notes=notes,
            expected_pairs=expected_pairs,
            dataset_version=version
        )

    def load_documents(self) -> List[DocumentInput]:
        documents = []
        # Find all .md files recursively in documents_root
        for root, _, files in os.walk(self.documents_root):
            for file in files:
                if file.endswith(".md"):
                    abs_path = Path(root) / file
                    # We want relative path matching the fixture note_paths (which start with "tests/fixtures/relation_vault/...")
                    # Let's find relative path from the project root.
                    # Wait, project root is d:/webproject/vault-steward
                    # Let's make it relative to the workspace root!
                    # The note_path in YAML is "tests/fixtures/relation_vault/..."
                    # We can use os.path.relpath(abs_path, start="d:/webproject/vault-steward") or starting from project root.
                    # Let's use relative path from the workspace/project root.
                    project_root = Path("d:/webproject/vault-steward").resolve()
                    abs_path_resolved = abs_path.resolve()
                    rel_path_str = abs_path_resolved.relative_to(project_root).as_posix()
                    
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    documents.append(DocumentInput(
                        note_path=rel_path_str,
                        content=content,
                        content_hash=content_hash
                    ))
        # Ensure deterministic ordering of documents
        documents.sort(key=lambda d: d.note_path)
        return documents
