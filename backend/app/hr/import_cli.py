from __future__ import annotations

from collections import Counter

from .importers import OfficialJobSnapshot


def inspect_snapshot(payload: bytes) -> dict[str, object]:
    snapshot = OfficialJobSnapshot.parse(payload)
    return {
        "version": snapshot.version,
        "last_successful_sync_at": snapshot.last_successful_sync_at.isoformat(),
        "job_count": len(snapshot.jobs),
        "statuses": dict(sorted(Counter(job.status for job in snapshot.jobs).items())),
    }
