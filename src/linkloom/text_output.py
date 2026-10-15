"""Atomic UTF-8 output shared by deterministic read-only CLI reports."""

import os
import tempfile
from pathlib import Path


def write_text(path: Path, content: str) -> None:
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    )
    try:
        with temporary_file:
            temporary_file.write(content)
        os.replace(temporary_file.name, path)
    except Exception:
        Path(temporary_file.name).unlink(missing_ok=True)
        raise
