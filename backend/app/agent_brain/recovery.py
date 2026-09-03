from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from app.execution_relay.models import SearchRecoveryPayload


class SearchRecoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SearchRecoveryState:
    status: Literal["unavailable", "no_results", "partial"]
    attempt_count: int
    last_attempt_at: datetime
    resumable: bool
    coverage_note: str | None

    def __post_init__(self) -> None:
        if (
            self.status not in {"unavailable", "no_results", "partial"}
            or type(self.attempt_count) is not int
            or not 1 <= self.attempt_count <= 100
            or not isinstance(self.last_attempt_at, datetime)
            or self.last_attempt_at.tzinfo is None
            or type(self.resumable) is not bool
            or (
                self.coverage_note is not None
                and (
                    type(self.coverage_note) is not str
                    or not self.coverage_note.strip()
                    or len(self.coverage_note.encode("utf-8")) > 4096
                )
            )
        ):
            raise SearchRecoveryError("search recovery state invalid")

    @property
    def can_resume(self) -> bool:
        return self.resumable and self.status in {"unavailable", "partial"}

    def public_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at.isoformat(),
            "resumable": self.resumable,
            "coverage_note": self.coverage_note,
        }


def search_recovery_from_collaboration(
    collaboration: object,
) -> SearchRecoveryState | None:
    if collaboration is None:
        return None
    if (
        not isinstance(collaboration, dict)
        or collaboration.get("contract_version") != "core_chat_collaboration_v4"
    ):
        raise SearchRecoveryError("search recovery contract invalid")
    recovery = collaboration.get("recovery")
    if recovery is None:
        return None
    try:
        parsed = SearchRecoveryPayload.model_validate_json(
            json.dumps(
                recovery,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            strict=True,
        )
        return SearchRecoveryState(
            status=parsed.status,
            attempt_count=parsed.attempt_count,
            last_attempt_at=parsed.last_attempt_at,
            resumable=parsed.resumable,
            coverage_note=parsed.coverage_note,
        )
    except (TypeError, UnicodeError, ValueError, ValidationError):
        raise SearchRecoveryError("search recovery contract invalid") from None
