from __future__ import annotations

# Pytest fixture imports intentionally enter this module's namespace.
# ruff: noqa: F401,F811
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from test_attachment_erasure_hotfix_database import (
    _codec,
    _insert_erasure_job,
    _seed_object_graph,
)
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import _insert_attachment, _seed_task

from app.attachments.erasure import AttachmentErasureError, AttachmentErasureRepository
from app.attachments.fence_capability import (
    AttachmentFenceCapabilityError,
    require_attachment_fence_capability,
)

MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations"
    / "106_attachment_erasure_write_fence.sql"
)


@pytest.mark.postgres
def test_106_capability_is_readable_by_both_payload_runtimes(control_database):
    for environment in control_database["environments"].values():
        suffix = "_preview" if environment["database"].endswith("_preview") else ""
        for purpose, role in (
            ("app", "platform_control_app" + suffix),
            ("brain", "platform_brain_worker" + suffix),
            ("maintenance", "platform_control_maintenance" + suffix),
        ):
            require_attachment_fence_capability(
                environment["urls"][role], purpose=purpose
            )


@pytest.mark.postgres
def test_106_capability_rejects_revoked_maintenance_column(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "revoke select (derivative_kind) on "
            "platform_attachments.processing_jobs from platform_control_maintenance"
        )
        admin.commit()
    try:
        with pytest.raises(AttachmentFenceCapabilityError):
            require_attachment_fence_capability(
                environment["urls"]["platform_control_app"], purpose="app"
            )
    finally:
        with psycopg.connect(environment["admin"]) as admin:
            admin.execute(
                "grant select (derivative_kind) on "
                "platform_attachments.processing_jobs to platform_control_maintenance"
            )
            admin.commit()


@pytest.mark.postgres
def test_106_grants_only_derive_key_identity_columns_to_maintenance(control_database):
    assert MIGRATION.is_file()
    for environment in control_database["environments"].values():
        maintenance = environment["roles"][5]
        with psycopg.connect(environment["admin"]) as admin:
            actual = {
                column
                for (column,) in admin.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='platform_attachments' "
                    "and table_name='processing_jobs' "
                    "and has_column_privilege(%s,'platform_attachments.processing_jobs',"
                    "column_name,'SELECT')",
                    (maintenance,),
                )
            }
        assert actual == {
            "attachment_id",
            "processing_job_id",
            "job_kind",
            "derivative_kind",
        }
        suffix = "_preview" if environment["database"].endswith("_preview") else ""
        for role in (
            "platform_control_app" + suffix,
            "platform_brain_worker" + suffix,
            "platform_control_maintenance" + suffix,
        ):
            with psycopg.connect(environment["admin"]) as admin:
                ledger_columns = {
                    column
                    for (column,) in admin.execute(
                        "select column_name from information_schema.columns "
                        "where table_schema='platform_control' "
                        "and table_name='schema_migrations' "
                        "and has_column_privilege(%s,"
                        "'platform_control.schema_migrations',column_name,'SELECT')",
                        (role,),
                    )
                }
            assert {"version", "sha256"} <= ledger_columns


@pytest.mark.postgres
def test_claim_logically_deletes_but_retains_refs_and_unrecorded_derive_key(
    control_database,
):
    environment = control_database["environments"]["production"]
    codec = _codec()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id, erasure_job_id, known_refs = _seed_object_graph(
            admin, codec, context
        )
        processing_job_id = uuid4()
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,derivative_kind,state," 
            "completed_at) values (%s,%s,'derive','preview','completed',now())",
            (processing_job_id, attachment_id),
        )
        before = admin.execute(
            "select object_ref_ciphertext,object_ref_key_version from "
            "platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone()
        admin.commit()

    repository = AttachmentErasureRepository(
        environment["urls"]["platform_control_maintenance"], content_codec=codec
    )
    job = repository.claim("fence-claimer")

    from app.attachments import worker

    assert job is not None and job.erasure_job_id == erasure_job_id
    assert set(job.object_refs) == known_refs | {
        worker.derivative_object_key(processing_job_id, "preview")
    }
    with psycopg.connect(environment["admin"]) as admin:
        after = admin.execute(
            "select state,state_reason,deleted_at is not null,"
            "object_ref_ciphertext,object_ref_key_version from "
            "platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone()
    assert after[:3] == ("deleted", "erasure_pending", True)
    assert after[3:] == before


@pytest.mark.postgres
def test_runtime_permission_loss_rolls_back_claim_and_logical_delete(control_database):
    environment = control_database["environments"]["production"]
    codec = _codec()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id, erasure_job_id, _refs = _seed_object_graph(
            admin, codec, context
        )
        admin.execute(
            "revoke select (derivative_kind) on "
            "platform_attachments.processing_jobs from platform_control_maintenance"
        )
        admin.commit()

    repository = AttachmentErasureRepository(
        environment["urls"]["platform_control_maintenance"], content_codec=codec
    )
    try:
        with pytest.raises(AttachmentErasureError):
            repository.claim("permission-revoked")
    finally:
        with psycopg.connect(environment["admin"]) as admin:
            admin.execute(
                "grant select (derivative_kind) on "
                "platform_attachments.processing_jobs to platform_control_maintenance"
            )
            admin.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,claimed_by,attempt_count from "
            "platform_attachments.erasure_jobs where erasure_job_id=%s",
            (erasure_job_id,),
        ).fetchone() == ("queued", None, 0)
        assert admin.execute(
            "select state,state_reason,deleted_at from "
            "platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("ready", None, None)
        admin.execute(
            "delete from platform_attachments.erasure_jobs where erasure_job_id=%s",
            (erasure_job_id,),
        )
        admin.commit()


def _seed_running_scan(environment):
    processing_job_id = uuid4()
    attempt_token = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context, state="scanning")
        erasure_job_id = _insert_erasure_job(admin, context, attachment_id)
        admin.execute(
            "insert into platform_attachments.processing_jobs "
            "(processing_job_id,attachment_id,job_kind,state,attempt_count,"
            "claimed_by,claimed_at,attempt_token) "
            "values (%s,%s,'scan','running',1,'scan-worker',now(),%s)",
            (processing_job_id, attachment_id, attempt_token),
        )
        admin.commit()
    return attachment_id, erasure_job_id, processing_job_id, attempt_token


@pytest.mark.postgres
def test_scan_commit_first_closes_job_set_before_erasure_claim(control_database):
    environment = control_database["environments"]["production"]
    attachment_id, erasure_job_id, processing_job_id, attempt_token = (
        _seed_running_scan(environment)
    )

    def claim():
        with psycopg.connect(
            environment["urls"]["platform_control_maintenance"]
        ) as maintenance:
            row = maintenance.execute(
                "select * from platform_attachments."
                "claim_attachment_erasure_job_v64('after-scan')"
            ).fetchone()
            maintenance.commit()
            return row[0]

    with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
        brain.execute(
            "select platform_attachments.record_attachment_processing_result_v64("
            "%s,%s,'ready',null)",
            (processing_job_id, attempt_token),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(claim)
            time.sleep(0.1)
            assert not future.done(), "erasure claim must wait for scan attachment lock"
            brain.commit()
            assert future.result(timeout=3) == erasure_job_id

    with psycopg.connect(environment["admin"]) as admin:
        rows = admin.execute(
            "select processing_job_id,derivative_kind from "
            "platform_attachments.processing_jobs where attachment_id=%s "
            "and job_kind='derive'",
            (attachment_id,),
        ).fetchall()
        state = admin.execute(
            "select state,state_reason from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone()
    assert len(rows) == 1 and rows[0][1] == "preview"
    assert state == ("deleted", "erasure_pending")


@pytest.mark.postgres
def test_erasure_claim_first_rejects_scan_completion_without_new_derive(
    control_database,
):
    environment = control_database["environments"]["production"]
    attachment_id, erasure_job_id, processing_job_id, attempt_token = (
        _seed_running_scan(environment)
    )

    def finish_scan():
        with psycopg.connect(environment["urls"]["platform_brain_worker"]) as brain:
            try:
                brain.execute(
                    "select platform_attachments."
                    "record_attachment_processing_result_v64(%s,%s,'ready',null)",
                    (processing_job_id, attempt_token),
                )
                brain.commit()
                return None
            except Exception as error:  # noqa: BLE001 - return DB ordering outcome
                brain.rollback()
                return error

    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as maintenance:
        row = maintenance.execute(
            "select * from platform_attachments."
            "claim_attachment_erasure_job_v64('before-scan')"
        ).fetchone()
        assert row[0] == erasure_job_id
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(finish_scan)
            time.sleep(0.1)
            assert not future.done(), "scan result must wait for erasure attachment lock"
            maintenance.commit()
            error = future.result(timeout=3)

    assert isinstance(error, psycopg.errors.CheckViolation)
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_attachments.processing_jobs "
            "where attachment_id=%s and job_kind='derive'",
            (attachment_id,),
        ).fetchone() == (0,)
        assert admin.execute(
            "select state,state_reason from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("deleted", "erasure_pending")
