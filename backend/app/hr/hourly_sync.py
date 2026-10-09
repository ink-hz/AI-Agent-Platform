"""Atomically project a published official-job registry into existing HR libraries."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.local_secrets import read_secret_file

from .importers import OfficialJob, OfficialJobSnapshot


MAX_SNAPSHOT_BYTES = 32_000_000
MAX_SNAPSHOT_AGE = timedelta(minutes=75)
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_OWNERS = 200
MAX_JOBS = 2_000

_POSITION_SQL = (
    "select (platform_hr.project_official_position_v66("
    "%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)).*"
)
_VERSION_SQL = (
    "select (platform_hr.project_official_version_v69("
    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,"
    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)).*"
)


def validate_snapshot_age(snapshot: OfficialJobSnapshot, now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("sync clock invalid")
    age = now - snapshot.last_successful_sync_at
    if age > MAX_SNAPSHOT_AGE:
        raise ValueError("official snapshot stale")
    if age < -MAX_FUTURE_SKEW:
        raise ValueError("official snapshot from future")
    if not snapshot.jobs or len(snapshot.jobs) > MAX_JOBS or len(snapshot.version) > 256:
        raise ValueError("official snapshot bounds invalid")


def should_append_version(current: dict | None, job: OfficialJob) -> bool:
    if current is None or current.get("current_official_version_id") is None:
        return True
    return any((
        current.get("content_hash") != job.content_hash,
        current.get("official_status") != job.status,
        current.get("status_reason") != job.status_reason,
        current.get("official_status_code") != job.official_status,
    ))


def _existing(connection) -> tuple[tuple[UUID, ...], dict[tuple[UUID, str], dict]]:
    rows = connection.execute(
        "select p.owner_internal_user_id,p.position_id,p.official_job_id,"
        "p.source_version,p.source_synced_at,p.current_official_version_id,"
        "v.content_hash,v.official_status,v.status_reason,v.official_status_code "
        "from platform_hr.positions p left join platform_hr.official_position_versions v "
        "on v.official_position_version_id=p.current_official_version_id "
        "and v.owner_internal_user_id=p.owner_internal_user_id "
        "where p.source_kind='official_site' "
        "order by p.owner_internal_user_id,p.official_job_id"
    ).fetchall()
    owners = tuple(dict.fromkeys(row["owner_internal_user_id"] for row in rows))
    if not owners or len(owners) > MAX_OWNERS:
        raise ValueError("official library owner scope invalid")
    return owners, {
        (row["owner_internal_user_id"], row["official_job_id"]): row for row in rows
    }


def project_snapshot(connection, snapshot: OfficialJobSnapshot) -> dict[str, object]:
    """One transaction is owned by the caller; SQL functions enforce HR ownership."""
    connection.execute("select pg_advisory_xact_lock(hashtextextended('hr-official-hourly-sync',0))")
    owners, existing = _existing(connection)
    job_ids = {job.canonical_id for job in snapshot.jobs}
    if any(job_id not in job_ids for _, job_id in existing):
        raise ValueError("published registry omits an existing official job")
    if any(
        row["source_synced_at"] is not None
        and row["source_synced_at"] > snapshot.last_successful_sync_at
        for row in existing.values()
    ):
        raise ValueError("official snapshot older than published library")
    same_source_time = existing and all(
        row["source_synced_at"] == snapshot.last_successful_sync_at
        for row in existing.values()
    )
    if same_source_time and all(
        (owner, job.canonical_id) in existing
        and not should_append_version(existing[(owner, job.canonical_id)], job)
        for owner in owners for job in snapshot.jobs
    ):
        return {"status": "replayed", "owners": len(owners), "jobs": len(snapshot.jobs), "versions": 0}
    if same_source_time:
        raise ValueError("conflicting official snapshot at same source time")

    versions = 0
    for owner in owners:
        for job in snapshot.jobs:
            current = existing.get((owner, job.canonical_id))
            append = should_append_version(current, job)
            position_id = uuid5(owner, f"official-position:{job.canonical_id}")
            source_version = snapshot.version if append else current["source_version"]
            row = connection.execute(_POSITION_SQL, (
                position_id, owner,
                uuid5(owner, f"official-position-sync:{snapshot.version}:{job.canonical_id}"),
                job.canonical_id, job.title, job.organization,
                json.dumps(job.locations, ensure_ascii=False), job.status,
                source_version, job.content_hash, snapshot.last_successful_sync_at,
            )).fetchone()
            if row is None or row["position_id"] != position_id:
                raise ValueError("official position projection mismatch")
            if not append:
                continue
            evidence = {
                "job_ad_id": str(job.job_ad_id),
                "source_record_ids": list(job.source_record_ids),
                "snapshot_version": snapshot.version,
                "last_successful_sync_at": snapshot.last_successful_sync_at.isoformat(),
            }
            version_id = uuid5(position_id, f"official-version:{snapshot.version}:{job.content_hash}")
            version = connection.execute(_VERSION_SQL, (
                version_id, owner, position_id,
                uuid5(owner, f"official-version-sync:{snapshot.version}:{job.canonical_id}"),
                job.canonical_id, job.title, job.organization,
                json.dumps(job.locations, ensure_ascii=False), job.category,
                job.subcategory, job.headcount, job.degree, job.employment_type,
                job.salary, job.duty, job.requirement, snapshot.version,
                job.source_changed_at, job.content_hash, job.first_seen_at,
                job.last_seen_at, job.status, job.status_reason,
                json.dumps(evidence, ensure_ascii=False), job.consecutive_misses,
                job.official_status, snapshot.last_successful_sync_at,
            )).fetchone()
            if version is None or version["official_position_version_id"] != version_id:
                raise ValueError("official version projection mismatch")
            versions += 1
    return {"status": "imported", "owners": len(owners), "jobs": len(snapshot.jobs), "versions": versions}


def import_bytes(payload: bytes, database_url: str, *, now: datetime | None = None) -> dict[str, object]:
    snapshot = OfficialJobSnapshot.parse(payload)
    validate_snapshot_age(snapshot, now or datetime.now(UTC))
    with psycopg.connect(
        database_url, connect_timeout=5, options="-c statement_timeout=15000",
        row_factory=dict_row,
    ) as connection:
        result = project_snapshot(connection, snapshot)
    return {
        **result,
        "version": snapshot.version,
        "source_synced_at": snapshot.last_successful_sync_at.isoformat(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if not payload or len(payload) > MAX_SNAPSHOT_BYTES:
        print("HR_OFFICIAL_IMPORT_FAILED bounds", file=sys.stderr)
        return 1
    try:
        database_url = read_secret_file(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"])
        result = import_bytes(payload, database_url)
    except (KeyError, ValueError, psycopg.Error):
        print("HR_OFFICIAL_IMPORT_FAILED validation_or_database", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
