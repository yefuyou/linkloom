"""Reviewed personal/attribute facts, separate from decision memory (issue #71).

Everything here is inert unless ``LINKLOOM_FACT_MEMORY`` is on. With the switch
off no table is created, no route changes and no response field is added.
"""
from __future__ import annotations

import os

FLAG = "LINKLOOM_FACT_MEMORY"


def fact_memory_enabled() -> bool:
    return os.environ.get(FLAG, "").strip().casefold() in {"1", "on", "true", "yes"}
