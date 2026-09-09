"""Read-only access to the closed ``trajectory_kb_v1`` synthetic fixture."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import unicodedata


EXPOSED_FIXTURE_PATHS = (
    "n/agent-loop.md",
    "n/checkpoint.md",
    "n/empty.md",
    "n/memory-policy.md",
    "n/missing.md",
    "n/retrieval.md",
)
_EXPOSED_SET = frozenset(EXPOSED_FIXTURE_PATHS)
_HEADING_PATTERN = re.compile(r"^#{1,6}[ \t]+(.+?)\s*$")


def validate_fixture_reference(value: str) -> str:
    """Return a canonical synthetic-relative path or reject unsafe input."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("fixture path must be a non-empty string")
    if "gold" in value.casefold():
        raise ValueError("fixture path must never reference Gold")
    if "://" in value:
        raise ValueError("fixture path must not be a network URL")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("fixture path must be relative")

    canonical = value.replace("\\", "/")
    parts = PurePosixPath(canonical).parts
    if not parts or any(part in {".", "..", ""} for part in parts):
        raise ValueError("fixture path must not traverse outside trajectory_kb_v1")
    if canonical not in _EXPOSED_SET:
        raise ValueError("fixture path is not exposed by trajectory_kb_v1")
    return canonical


def stable_heading_slug(value: str) -> str:
    """Return the deterministic fragment slug used by trajectory evidence."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("heading must be non-empty text")
    heading = re.sub(r"\s+#+\s*$", "", value.strip())
    normalized = unicodedata.normalize("NFKC", heading).casefold()
    pieces: list[str] = []
    pending_separator = False
    for character in normalized:
        if character.isalnum():
            if pending_separator and pieces:
                pieces.append("-")
            pieces.append(character)
            pending_separator = False
        elif character.isspace() or character in {"-", "_"}:
            pending_separator = True
    slug = "".join(pieces).strip("-")
    if not slug:
        raise ValueError("heading has no stable slug")
    return slug


def validate_evidence_reference(
    value: str,
    repo_root: str | Path | None = None,
) -> str:
    """Validate ``path#anchor`` against a real heading in that exact fixture file."""

    if not isinstance(value, str) or value.count("#") != 1:
        raise ValueError("evidence reference must be path#anchor")
    raw_path, anchor = value.split("#", 1)
    canonical_path = validate_fixture_reference(raw_path)
    if not anchor.strip() or anchor != anchor.strip():
        raise ValueError("evidence reference anchor must be non-empty")
    root = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[4]
    )
    fixture_root = (root / "tests" / "fixtures" / "trajectory_kb_v1").resolve()
    target = (fixture_root / canonical_path).resolve()
    try:
        target.relative_to(fixture_root)
    except ValueError as exc:
        raise ValueError("evidence reference escaped the synthetic fixture") from exc
    if not target.is_file():
        raise ValueError("evidence reference fixture file is missing")
    slugs: set[str] = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        match = _HEADING_PATTERN.match(line)
        if match is not None:
            slugs.add(stable_heading_slug(match.group(1)))
    if anchor not in slugs:
        raise ValueError("evidence reference anchor is not a heading in its fixture file")
    return f"{canonical_path}#{anchor}"


class TrajectoryFixture:
    """Expose exactly six local notes and no vault, network, or Gold surface."""

    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.root = (
            self.repo_root / "tests" / "fixtures" / "trajectory_kb_v1"
        ).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError("trajectory_kb_v1 fixture root is missing")
        missing = [path for path in EXPOSED_FIXTURE_PATHS if not (self.root / path).is_file()]
        if missing:
            raise FileNotFoundError(
                f"trajectory_kb_v1 is missing exposed fixture files: {missing}"
            )

    def list_paths(self) -> list[str]:
        return list(EXPOSED_FIXTURE_PATHS)

    def resolve(self, relative_path: str) -> Path:
        canonical = validate_fixture_reference(relative_path)
        target = (self.root / canonical).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("fixture path escaped trajectory_kb_v1") from exc
        if not target.is_file():
            raise FileNotFoundError(f"synthetic fixture note is missing: {canonical}")
        return target

    def read_text(self, relative_path: str) -> str:
        return self.resolve(relative_path).read_text(encoding="utf-8")


__all__ = [
    "EXPOSED_FIXTURE_PATHS",
    "TrajectoryFixture",
    "stable_heading_slug",
    "validate_evidence_reference",
    "validate_fixture_reference",
]
