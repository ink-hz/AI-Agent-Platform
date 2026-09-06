from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[3].resolve()
DEFAULT_LOCAL_ROOT = Path(
    "/Users/neo/Library/Application Support/"
    "OrbbecAI-Agent-Platform/hr-intelligence"
)


def local_data_root() -> Path:
    raw = os.getenv("HR_INTELLIGENCE_LOCAL_ROOT")
    selected = Path(raw) if raw else DEFAULT_LOCAL_ROOT
    if not selected.is_absolute():
        raise ValueError("HR intelligence local root must be absolute")
    selected = selected.resolve()
    if REPOSITORY_ROOT in (selected, *selected.parents):
        raise ValueError("HR intelligence data must be outside repository")
    return selected


__all__ = ["DEFAULT_LOCAL_ROOT", "REPOSITORY_ROOT", "local_data_root"]
