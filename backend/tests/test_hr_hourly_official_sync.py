from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from test_control_plane_migration import control_database
from test_hr_position_importers import _job, _snapshot

from app.hr.import_cli import _ImportRepository
from app.hr.importers import OfficialJobSnapshot, project_official_jobs
from app.hr.hourly_sync import project_snapshot, should_append_version, validate_snapshot_age


def test_hourly_refresh_keeps_immutable_version_when_only_observation_changes() -> None:
    job = OfficialJobSnapshot.parse(_snapshot(_job())).jobs[0]
    current = {
        "content_hash": job.content_hash,
        "official_status": job.status,
        "status_reason": job.status_reason,
        "official_status_code": job.official_status,
        "current_official_version_id": "existing",
    }
    assert should_append_version(current, job) is False
    assert should_append_version({**current, "content_hash": "b" * 64}, job) is True
    assert should_append_version({**current, "official_status": "inactive"}, job) is True
    assert should_append_version({**current, "current_official_version_id": None}, job) is True


def test_hourly_import_rejects_stale_or_future_snapshots() -> None:
    snapshot = OfficialJobSnapshot.parse(_snapshot(_job()))
    now = snapshot.last_successful_sync_at + timedelta(minutes=15)
    validate_snapshot_age(snapshot, now)
    with pytest.raises(ValueError, match="stale"):
        validate_snapshot_age(snapshot, now + timedelta(minutes=76))
    with pytest.raises(ValueError, match="future"):
        validate_snapshot_age(snapshot, snapshot.last_successful_sync_at - timedelta(minutes=6))


@pytest.mark.postgres
def test_hourly_projection_updates_every_existing_owner_without_version_churn(control_database) -> None:
    environment = control_database["environments"]["production"]
    owners = (uuid4(), uuid4())
    with psycopg.connect(environment["admin"]) as admin:
        for owner in owners:
            admin.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'HR hourly owner','active')",
                (owner,),
            )
    repository = _ImportRepository(environment["urls"]["platform_control_app"])
    first = OfficialJobSnapshot.parse(_snapshot(_job()))
    for owner in owners:
        project_official_jobs(first, repository, owner, uuid4())

    payload = json.loads(_snapshot(_job()))
    payload["version"] = "20260904T020000Z-new"
    payload["lastSuccessfulSyncAt"] = "2026-09-04T02:00:00.000Z"
    payload["jobs"][0]["lastSeenAt"] = "2026-09-04T02:00:00.000Z"
    payload["jobs"].append(_job("J22014", jobAdId=22014, title="新增官网岗位"))
    second = OfficialJobSnapshot.parse(json.dumps(payload).encode())

    with psycopg.connect(environment["urls"]["platform_control_app"], row_factory=dict_row) as connection:
        result = project_snapshot(connection, second)
    assert result == {"status": "imported", "owners": 2, "jobs": 2, "versions": 2}

    with psycopg.connect(environment["urls"]["platform_control_app"], row_factory=dict_row) as connection:
        replay = project_snapshot(connection, second)
        rows = connection.execute(
            "select owner_internal_user_id,official_job_id,source_synced_at,row_version "
            "from platform_hr.positions where source_kind='official_site'"
        ).fetchall()
        version_count = connection.execute(
            "select count(*) from platform_hr.official_position_versions"
        ).fetchone()["count"]
    assert replay["status"] == "replayed"
    assert len(rows) == 4
    assert all(row["source_synced_at"] == second.last_successful_sync_at for row in rows)
    assert version_count == 4  # old and new for two owners, no observation-only versions

    # Equal source times with changed facts cannot be ordered reliably by the
    # immutable version pointer, so the whole conflicting snapshot is rejected.
    payload["version"] = "20260904T020000Z-corrected"
    payload["jobs"][0]["contentHash"] = "c" * 64
    corrected = OfficialJobSnapshot.parse(json.dumps(payload).encode())
    with pytest.raises(ValueError, match="same source time"):
        with psycopg.connect(environment["urls"]["platform_control_app"], row_factory=dict_row) as connection:
            project_snapshot(connection, corrected)


@pytest.mark.postgres
def test_hourly_projection_rolls_back_whole_batch_on_later_failure(control_database) -> None:
    environment = control_database["environments"]["production"]
    owner = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'HR rollback owner','active')",
            (owner,),
        )
    repository = _ImportRepository(environment["urls"]["platform_control_app"])
    project_official_jobs(OfficialJobSnapshot.parse(_snapshot(_job())), repository, owner, uuid4())
    payload = json.loads(_snapshot(_job()))
    payload["version"] = "20260904T020000Z-new"
    payload["lastSuccessfulSyncAt"] = "2026-09-04T02:00:00.000Z"
    payload["jobs"].append(_job("J22014", jobAdId=22014))
    snapshot = OfficialJobSnapshot.parse(json.dumps(payload).encode())

    class FailAfterFirstProjection:
        def __init__(self, connection):
            self.connection = connection
            self.position_calls = 0

        def execute(self, query, params=None):
            if "project_official_position_v66" in query:
                self.position_calls += 1
                if self.position_calls == 2:
                    raise RuntimeError("injected later projection failure")
            return self.connection.execute(query, params)

    with pytest.raises(RuntimeError, match="later projection"):
        with psycopg.connect(environment["urls"]["platform_control_app"], row_factory=dict_row) as connection:
            project_snapshot(FailAfterFirstProjection(connection), snapshot)
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        rows = connection.execute(
            "select official_job_id,source_synced_at from platform_hr.positions "
            "where owner_internal_user_id=%s", (owner,)
        ).fetchall()
    assert rows == [("J11014", OfficialJobSnapshot.parse(_snapshot()).last_successful_sync_at)]
