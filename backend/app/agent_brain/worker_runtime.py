from __future__ import annotations

from typing import Literal


WorkerMode = Literal["brain", "adapter", "reaper", "all"]


def validate_worker_mode(value: str) -> WorkerMode:
    if value not in {"brain", "adapter", "reaper", "all"}:
        raise ValueError("Brain worker mode invalid")
    return value  # type: ignore[return-value]
