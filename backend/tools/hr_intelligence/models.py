from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.hr.panorama_models import canonical_panorama_url


_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_COMPANY_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")


def _required_text(value: object, maximum: int, label: str) -> str:
    selected = value.strip() if isinstance(value, str) else ""
    if not selected or len(selected) > maximum:
        raise ValueError(f"{label} invalid")
    return selected


@dataclass(frozen=True, slots=True)
class NormalizedJob:
    job_id: UUID
    source_id: UUID
    company_key: str
    public_job_key: str
    title: str
    location: str
    duty_excerpt: str
    requirement_excerpt: str
    source_url: str
    evidence_sha256: str
    observed_at: datetime
    status: str = "open"

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, UUID) or not isinstance(self.source_id, UUID):
            raise TypeError("normalized job identity invalid")
        company_key = _required_text(self.company_key, 128, "company key")
        if _COMPANY_KEY.fullmatch(company_key) is None:
            raise ValueError("company key invalid")
        object.__setattr__(self, "company_key", company_key)
        for name, maximum in (
            ("public_job_key", 512),
            ("title", 1000),
            ("location", 1000),
            ("duty_excerpt", 32768),
            ("requirement_excerpt", 32768),
        ):
            object.__setattr__(
                self,
                name,
                _required_text(getattr(self, name), maximum, "normalized job"),
            )
        object.__setattr__(
            self,
            "source_url",
            canonical_panorama_url(self.source_url),
        )
        if not isinstance(self.evidence_sha256, str) or _SHA256.fullmatch(
            self.evidence_sha256
        ) is None:
            raise ValueError("normalized job evidence invalid")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("normalized job observation invalid")
        if self.status not in {"open", "closed", "unknown"}:
            raise ValueError("normalized job status invalid")

    @property
    def snapshot_id(self) -> UUID:
        return self.job_id


__all__ = ["NormalizedJob", "canonical_panorama_url"]
