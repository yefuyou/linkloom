"""Shared pytest defaults for portable temporary artifact paths."""
from __future__ import annotations

import os
from uuid import uuid4


def pytest_configure(config):
    # Runtime artifacts add nested model/turn/tool-result directories. Keep
    # Windows fixture roots short even when TEMP points inside a deep venv.
    # A fresh directory also avoids deleting another pytest run's artifacts.
    if os.name == "nt" and config.option.basetemp is None:
        config.option.basetemp = str(
            config.rootpath / ".tmp" / ("pt-" + uuid4().hex[:8])
        )
