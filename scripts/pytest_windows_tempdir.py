"""Windows-only pytest temp-dir mode workaround for the managed workspace."""

from __future__ import annotations

import os
from pathlib import Path


_BASE_TEMP_VALUE = os.environ.get("LINKLOOM_PYTEST_BASETEMP")
_BASE_TEMP = (
    Path(_BASE_TEMP_VALUE).resolve(strict=False)
    if _BASE_TEMP_VALUE
    else None
)
_ORIGINAL_MKDIR = Path.mkdir


def _mkdir_with_workspace_acl(
    self: Path,
    mode: int = 0o777,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    if os.name == "nt" and mode == 0o700 and _BASE_TEMP is not None:
        target = self.resolve(strict=False)
        try:
            target.relative_to(_BASE_TEMP)
        except ValueError:
            pass
        else:
            # Pytest's owner-only mode leaves this Windows sandbox unable to
            # initialize tmp_path. Use the normal inherited directory ACL,
            # but only under the runner's explicit per-run basetemp.
            mode = 0o777
    _ORIGINAL_MKDIR(self, mode=mode, parents=parents, exist_ok=exist_ok)


Path.mkdir = _mkdir_with_workspace_acl
