"""Small local artifact store for durable model exchanges."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
import re
import tempfile
from typing import Any

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    _assert_json_safe_primitive,
    _assert_no_forbidden_persisted_keys,
)


ARTIFACT_KINDS = frozenset({"request", "response", "observation", "tool_definitions"})
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ArtifactRef:
    """Relative artifact identity and content integrity metadata."""

    ref: str
    sha256: str
    kind: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.ref or Path(self.ref).is_absolute() or PureWindowsPath(self.ref).is_absolute():
            raise ValidationError("ArtifactRef.ref must be a relative path.")
        if any(part == ".." for part in self.ref.replace("\\", "/").split("/")):
            raise ValidationError("ArtifactRef.ref must not traverse outside the artifact root.")
        if self.kind not in ARTIFACT_KINDS:
            raise ValidationError("ArtifactRef.kind is not a supported model artifact kind.")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValidationError("ArtifactRef.sha256 must be a lowercase SHA-256 string.")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValidationError("ArtifactRef.size_bytes must be a non-negative integer.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelArtifactStore:
    """Write-once, JSON-only artifacts under one caller-owned root."""

    def __init__(self, root: Path | str) -> None:
        if not isinstance(root, (Path, str)):
            raise ValidationError("ModelArtifactStore.root must be a path.")
        self.root = Path(root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._root_resolved = self.root.resolve()
        except OSError as error:
            raise ValidationError("Model artifact root is not available.") from error

    @staticmethod
    def safe_segment(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("Artifact identity segment must be non-empty text.")
        segment = _SAFE_SEGMENT.sub("_", value.strip()).strip("._")
        if not segment:
            raise ValidationError("Artifact identity segment is not safe.")
        return segment[:128]

    def _path_for(self, ref: str) -> Path:
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError("Artifact reference must be non-empty text.")
        normalized = ref.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or PureWindowsPath(normalized).is_absolute():
            raise ValidationError("Artifact reference must be relative.")
        if any(part in {"", "."} for part in normalized.split("/")):
            raise ValidationError("Artifact reference contains an invalid path segment.")
        if any(part == ".." for part in normalized.split("/")):
            raise ValidationError("Artifact reference must not traverse outside the root.")
        path = self.root.joinpath(*normalized.split("/"))
        try:
            path.resolve().relative_to(self._root_resolved)
        except ValueError as error:
            raise ValidationError("Artifact reference escapes the artifact root.") from error
        return path

    @staticmethod
    def _encode(payload: dict[str, Any]) -> bytes:
        if not isinstance(payload, dict):
            raise ValidationError("Model artifact payload must be a JSON object.")
        _assert_json_safe_primitive(payload, "model_artifact")
        _assert_no_forbidden_persisted_keys(payload, "model_artifact")
        try:
            return (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValidationError("Model artifact payload is not deterministically serializable.") from error

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def write(self, ref: str, payload: dict[str, Any], *, kind: str) -> ArtifactRef:
        if kind not in ARTIFACT_KINDS:
            raise ValidationError("Model artifact kind is not supported.")
        path = self._path_for(ref)
        normalized_ref = path.relative_to(self.root).as_posix()
        content = self._encode(payload)
        digest = self._hash(content)
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as error:
                raise ValidationError("Existing model artifact could not be read.") from error
            if self._hash(existing) != digest:
                raise ValidationError(
                    "Model artifact reference already contains different content.",
                    details={"reason": "artifact_conflict", "ref": normalized_ref},
                )
            return ArtifactRef(normalized_ref, digest, kind, len(existing))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, path)
            finally:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    temporary_path.unlink()
        except OSError as error:
            raise ValidationError("Model artifact could not be written.") from error
        return ArtifactRef(normalized_ref, digest, kind, len(content))

    def read(self, ref: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        path = self._path_for(ref)
        if expected_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValidationError("Expected artifact hash must be a lowercase SHA-256 string.")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ValidationError("Model artifact could not be read.") from error
        digest = self._hash(content)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValidationError(
                "Model artifact integrity verification failed.",
                details={"reason": "hash_mismatch", "ref": path.relative_to(self.root).as_posix()},
            )
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("Model artifact is not valid UTF-8 JSON.") from error
        if not isinstance(payload, dict):
            raise ValidationError("Model artifact root must be a JSON object.")
        _assert_json_safe_primitive(payload, "model_artifact")
        _assert_no_forbidden_persisted_keys(payload, "model_artifact")
        return payload

    def write_request(self, run_id: str, turn_id: str, payload: dict[str, Any]) -> ArtifactRef:
        return self.write(
            f"model/{self.safe_segment(run_id)}/{self.safe_segment(turn_id)}/request.json",
            payload,
            kind="request",
        )

    def write_response(self, run_id: str, turn_id: str, payload: dict[str, Any]) -> ArtifactRef:
        return self.write(
            f"model/{self.safe_segment(run_id)}/{self.safe_segment(turn_id)}/response.json",
            payload,
            kind="response",
        )

    def write_observation(self, run_id: str, turn_id: str, payload: dict[str, Any]) -> ArtifactRef:
        return self.write(
            f"model/{self.safe_segment(run_id)}/{self.safe_segment(turn_id)}/observation.json",
            payload,
            kind="observation",
        )


__all__ = ["ARTIFACT_KINDS", "ArtifactRef", "ModelArtifactStore"]
