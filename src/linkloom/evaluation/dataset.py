"""Read-only loader for the frozen relation_vault_v1 evaluation set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .models import DatasetManifest, GoldNote, GoldPair, InferenceDocument, InferencePair


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


class InferenceDataset:
    """Load only inference documents and candidates without Gold access."""

    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.fixture_root = self.repo_root / "tests" / "fixtures" / "relation_vault"
        if not self.fixture_root.is_dir():
            raise FileNotFoundError("relation_vault_v1 inputs are missing")
        
        self._load()

    def _load(self) -> None:
        fixture_paths = sorted(self.fixture_root.rglob("*.md"), key=lambda path: path.relative_to(self.repo_root).as_posix())
        if len(fixture_paths) != 10:
            raise ValueError(f"relation_vault_v1 requires exactly 10 fixture documents, got {len(fixture_paths)}")

        self.documents: list[InferenceDocument] = []
        snapshot_parts: list[str] = []
        for path in fixture_paths:
            relative = path.relative_to(self.repo_root).as_posix()
            content = path.read_text(encoding="utf-8")
            content_sha = _sha256_text(content)
            self.documents.append(InferenceDocument(relative, content_sha, content))
            snapshot_parts.append(f"{relative}\0{content_sha}\n")

        self.dataset_version = "relation_vault_v1"
        self._fixture_snapshot = {doc.path: doc.content_sha256 for doc in self.documents}
        self._fixture_sha256 = _sha256_text("".join(snapshot_parts))

    def get_inference_documents(self) -> List[InferenceDocument]:
        return list(self.documents)

    def get_inference_pairs(self) -> List[InferencePair]:
        return [
            InferencePair(self.documents[left].path, self.documents[right].path)
            for left in range(len(self.documents))
            for right in range(left + 1, len(self.documents))
        ]


class EvaluationDataset:
    """Load evaluator-only Gold from immutable inputs."""

    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.gold_path = self.repo_root / "tests" / "eval" / "relation_gold.yaml"
        self.fixture_root = self.repo_root / "tests" / "fixtures" / "relation_vault"
        if not self.gold_path.is_file() or not self.fixture_root.is_dir():
            raise FileNotFoundError("relation_vault_v1 inputs are missing")
        self.inference = InferenceDataset(self.repo_root)
        self.documents = self.inference.documents
        self._load()

    def _load(self) -> None:
        gold_bytes = self.gold_path.read_bytes()
        try:
            gold_data = yaml.safe_load(gold_bytes.decode("utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError("Frozen Gold YAML is invalid") from exc
        if gold_data.get("dataset_version") != "relation_vault_v1":
            raise ValueError("Unsupported evaluation dataset version")

        self.gold_notes = [GoldNote(**item) for item in gold_data.get("notes", [])]
        pairs: list[GoldPair] = []
        for item in gold_data.get("expected_pairs", []):
            pair_data = dict(item)
            if pair_data.get("relation_type") == "false-positive":
                pair_data["should_link"] = False
            pairs.append(GoldPair(**pair_data))
        self.gold_pairs = sorted(pairs, key=lambda pair: (pair.source, pair.target))
        self.gold_notes.sort(key=lambda note: note.note_path)

        expected_paths = {note.note_path for note in self.gold_notes}
        actual_paths = set(self.inference._fixture_snapshot.keys())
        if expected_paths != actual_paths:
            raise ValueError("Gold note paths do not exactly match the frozen fixture tree")

        if not self.gold_pairs or len(self.gold_pairs) > 45:
            raise ValueError("relation_vault_v1 Gold pair annotations are invalid")
        self.dataset_version = "relation_vault_v1"
        self._fixture_snapshot = self.inference._fixture_snapshot
        self._gold_sha256 = _sha256_bytes(gold_bytes)
        self._fixture_sha256 = self.inference._fixture_sha256
        self._initial_manifest = DatasetManifest(
            dataset_version=self.dataset_version,
            document_count=len(self.documents),
            pair_count=len(self.get_inference_pairs()),
            fixture_sha256=self._fixture_sha256,
            gold_sha256=self._gold_sha256,
            frozen=True,
        )

    def get_inference_documents(self) -> List[InferenceDocument]:
        return self.inference.get_inference_documents()

    def get_inference_pairs(self) -> List[InferencePair]:
        return self.inference.get_inference_pairs()

    def get_gold_notes(self) -> List[GoldNote]:
        return list(self.gold_notes)

    def get_gold_pairs(self) -> List[GoldPair]:
        return list(self.gold_pairs)

    def get_manifest(self) -> DatasetManifest:
        return DatasetManifest(
            dataset_version=self.dataset_version,
            document_count=len(self.documents),
            pair_count=len(self.get_inference_pairs()),
            fixture_sha256=self._current_fixture_digest(),
            gold_sha256=_sha256_bytes(self.gold_path.read_bytes()),
            frozen=True,
        )

    def _current_fixture_digest(self) -> str:
        parts: list[str] = []
        for path in sorted(self.fixture_root.rglob("*.md"), key=lambda item: item.relative_to(self.repo_root).as_posix()):
            relative = path.relative_to(self.repo_root).as_posix()
            parts.append(f"{relative}\0{_sha256_text(path.read_text(encoding='utf-8'))}\n")
        return _sha256_text("".join(parts))

    def assert_frozen(self, expected_manifest: DatasetManifest | None = None) -> None:
        expected = expected_manifest or self._initial_manifest
        current = self.get_manifest()
        if current.fixture_sha256 != expected.fixture_sha256:
            raise ValueError(f"Fixture digest mismatch: expected {expected.fixture_sha256}")
        if current.gold_sha256 != expected.gold_sha256:
            raise ValueError(f"Gold digest mismatch: expected {expected.gold_sha256}")
        if current.dataset_version != expected.dataset_version:
            raise ValueError("Dataset version mismatch")
        if current.document_count != expected.document_count:
            raise ValueError("Document count mismatch")
        if current.pair_count != expected.pair_count:
            raise ValueError("Pair count mismatch")
        if current.document_count != len(self._fixture_snapshot) or current.document_count != 10:
            raise ValueError("Fixture tree shape changed")
