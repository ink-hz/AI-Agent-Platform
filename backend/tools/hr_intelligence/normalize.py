from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

from .collectors import CollectionResult
from .models import NormalizedJob
from .taxonomy import normalize_location

_COMPANY_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")


def normalize_jobs(
    result: CollectionResult,
    *,
    company_key: str,
) -> tuple[NormalizedJob, ...]:
    selected_company = company_key.strip() if isinstance(company_key, str) else ""
    if _COMPANY_KEY.fullmatch(selected_company) is None:
        raise ValueError("company key invalid")
    jobs = []
    for job in result.jobs:
        location = normalize_location(job.location)
        jobs.append(
            NormalizedJob(
            job_id=uuid5(
                NAMESPACE_URL,
                f"orbbec:hr-intelligence:job:{selected_company}:{job.public_job_key}",
            ),
            source_id=result.target.source_id,
            company_key=selected_company,
            public_job_key=job.public_job_key,
            title=job.title,
            location=location.normalized,
            duty_excerpt=job.duty_excerpt,
            requirement_excerpt=job.requirement_excerpt,
            source_url=job.source_url,
            evidence_sha256=result.evidence.sha256,
            observed_at=result.observed_at,
            status=job.status,
            raw_location=location.raw,
        )
        )
    return tuple(jobs)


__all__ = ["normalize_jobs"]
