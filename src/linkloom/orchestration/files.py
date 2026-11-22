"""Small durable-file helper shared by the orchestration modules."""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json(path, data):
    """Atomic replace: an interrupted write leaves the previous file intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None
