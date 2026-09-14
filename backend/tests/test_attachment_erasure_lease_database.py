from __future__ import annotations

# Pytest fixture imports intentionally enter this module's namespace.
# ruff: noqa: F401,F811
import multiprocessing
import os
from uuid import UUID, uuid4

import psycopg
import pytest
from app.attachments.erasure import AttachmentErasureError, AttachmentErasureRepository
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import _insert_attachment, _seed_task


def _codec(byte: bytes = b"7") -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=7,
            purpose="platform-content-encryption",
            _keys={7: byte * 32},
        )
    )


def _job(connection, context, attachment_id, *, max_attempts=5):
    job_id = uuid4()
    connection.execute(
        "insert into platform_attachments.erasure_jobs ("
        "erasure_job_id,attachment_id,requested_by_internal_user_id,"
        "reason_ciphertext,reason_key_version,reason_sha256,max_attempts,available_at) "
        "values (%s,%s,%s,%s,1,%s,%s,clock_timestamp()-interval '1 day')",
        (job_id, attachment_id, context["owner_id"], b"r" * 29, b"r" * 32, max_attempts),
    )
    connection.commit()
    return job_id


def _claim(connection, worker):
    row = connection.execute(
        "select erasure_job_id,attachment_id,attempt_token,attempt_count "
        "from platform_attachments.claim_attachment_erasure_job_v107(%s)",
        (worker,),
    ).fetchone()
    connection.commit()
    return None if row is None or row[0] is None else row


def _expire(admin, job_id):
    admin.execute(
        "update platform_attachments.erasure_jobs set "
        "lease_expires_at=clock_timestamp()-interval '1 second' "
        "where erasure_job_id=%s",
        (job_id,),
    )
    admin.commit()


def _claim_and_die(dsn, output):
    with psycopg.connect(dsn) as connection:
        row = _claim(connection, "doomed-process")
        output.send(str(row[2]))
        output.close()
    os._exit(71)


@pytest.mark.postgres
def test_killed_claim_is_recovered_with_new_token_and_old_results_are_fenced(
    control_database,
):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        job_id = _job(admin, context, attachment_id)
    receiver, sender = multiprocessing.Pipe(duplex=False)
    child = multiprocessing.Process(
        target=_claim_and_die,
        args=(environment["urls"]["platform_control_maintenance"], sender),
    )
    try:
        child.start()
        sender.close()
        assert receiver.poll(5), "claiming child did not publish its attempt token"
        old_token = UUID(receiver.recv())
        child.join(5)
        assert child.exitcode == 71
    finally:
        if child.is_alive():
            child.terminate()
            child.join(2)
        if child.is_alive():
            child.kill()
            child.join(2)
        receiver.close()

    with psycopg.connect(environment["admin"]) as admin:
        _expire(admin, job_id)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        with pytest.raises(psycopg.Error):
            maintenance.execute(
                "select platform_attachments.record_attachment_erasure_result_v107("
                "%s,%s,'completed','erased','{}'::jsonb)",
                (job_id, old_token),
            )
        maintenance.rollback()
        reclaimed = _claim(maintenance, "replacement-process")
        assert reclaimed[0] == job_id and reclaimed[2] != old_token
        for state in ("completed", "partial"):
            with pytest.raises(psycopg.Error):
                maintenance.execute(
                    "select platform_attachments.record_attachment_erasure_result_v107("
                    "%s,%s,%s,'late','{}'::jsonb)",
                    (job_id, old_token, state),
                )
            maintenance.rollback()


@pytest.mark.postgres
def test_expired_last_crashed_attempt_becomes_visible_exhausted_and_keeps_refs(
    control_database,
):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        original = admin.execute(
            "select object_ref_ciphertext from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone()[0]
        job_id = _job(admin, context, attachment_id, max_attempts=1)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        token = _claim(maintenance, "last-attempt")[2]
    with psycopg.connect(environment["admin"]) as admin:
        _expire(admin, job_id)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        with pytest.raises(psycopg.Error):
            maintenance.execute(
                "select platform_attachments.record_attachment_erasure_result_v107("
                "%s,%s,'completed','late','{}'::jsonb)",
                (job_id, token),
            )
        maintenance.rollback()
        assert _claim(maintenance, "sweeper") is None
    with psycopg.connect(environment["admin"]) as admin:
        row = admin.execute(
            "select erasure.state,erasure.state_reason,erasure.attempt_count,"
            "erasure.completed_at is not null,attachment.object_ref_ciphertext,"
            "erasure.downstream_cleanup_status->>'exhausted' "
            "from platform_attachments.erasure_jobs erasure join "
            "platform_attachments.attachments attachment using (attachment_id) "
            "where erasure_job_id=%s",
            (job_id,),
        ).fetchone()
    assert row == ("failed", "erasure_attempts_exhausted", 1, True, original, "true")


@pytest.mark.postgres
def test_only_exhausted_job_has_idempotent_explicit_recovery_and_fresh_token(
    control_database,
):
    environment = control_database["environments"]["production"]
    recovery_id = uuid4()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        job_id = _job(admin, context, attachment_id, max_attempts=1)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        first_token = _claim(maintenance, "first")[2]
    with psycopg.connect(environment["admin"]) as admin:
        _expire(admin, job_id)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        assert _claim(maintenance, "exhaust") is None
        for _ in range(2):
            assert maintenance.execute(
                "select platform_attachments.recover_attachment_erasure_job_v107(%s,%s,2)",
                (job_id, recovery_id),
            ).fetchone() == (job_id,)
            maintenance.commit()
        with pytest.raises(psycopg.Error):
            maintenance.execute(
                "select platform_attachments.recover_attachment_erasure_job_v107(%s,%s,2)",
                (job_id, uuid4()),
            )
        maintenance.rollback()
        recovered = _claim(maintenance, "recovered")
    assert recovered[2] != first_token and recovered[3] == 1
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_attachments.erasure_jobs set attempt_count=max_attempts"
            " where erasure_job_id=%s",
            (job_id,),
        )
        _expire(admin, job_id)
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        assert _claim(maintenance, "exhaust-again") is None
        assert maintenance.execute(
            "select platform_attachments.recover_attachment_erasure_job_v107(%s,%s,2)",
            (job_id, recovery_id),
        ).fetchone() == (job_id,)
        maintenance.commit()
        with pytest.raises(psycopg.Error):
            maintenance.execute(
                "select platform_attachments.recover_attachment_erasure_job_v107(%s,%s,3)",
                (job_id, recovery_id),
            )
        maintenance.rollback()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,state_reason,attempt_count,max_attempts from "
            "platform_attachments.erasure_jobs where erasure_job_id=%s",
            (job_id,),
        ).fetchone() == ("failed", "erasure_attempts_exhausted", 2, 2)
        assert admin.execute(
            "select count(*) from platform_attachments.erasure_recoveries "
            "where recovery_id=%s",
            (recovery_id,),
        ).fetchone() == (1,)
    second_recovery = uuid4()
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        assert maintenance.execute(
            "select platform_attachments.recover_attachment_erasure_job_v107(%s,%s,1)",
            (job_id, second_recovery),
        ).fetchone() == (job_id,)
        maintenance.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,attempt_count,max_attempts,"
            "downstream_cleanup_status->>'recovery_count' from "
            "platform_attachments.erasure_jobs where erasure_job_id=%s",
            (job_id,),
        ).fetchone() == ("partial", 0, 1, "2")


@pytest.mark.postgres
def test_reference_decryption_failure_consumes_bounded_attempt_and_keeps_evidence(
    control_database,
):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        original = admin.execute(
            "select object_ref_ciphertext from platform_attachments.attachments "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone()[0]
        job_id = _job(admin, context, attachment_id, max_attempts=1)
    repository = AttachmentErasureRepository(
        environment["urls"]["platform_control_maintenance"],
        content_codec=_codec(b"x"),
    )
    with pytest.raises(AttachmentErasureError):
        repository.claim("bad-keyring")
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select erasure.state,erasure.state_reason,erasure.attempt_count,"
            "attachment.object_ref_ciphertext from platform_attachments.erasure_jobs erasure "
            "join platform_attachments.attachments attachment using (attachment_id) "
            "where erasure_job_id=%s",
            (job_id,),
        ).fetchone() == ("failed", "erasure_attempts_exhausted", 1, original)


@pytest.mark.postgres
def test_legacy_v64_claim_and_record_are_not_executable(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["urls"]["platform_control_maintenance"]) as maintenance:
        for query, params in (
            ("select platform_attachments.claim_attachment_erasure_job_v64(%s)", ("legacy",)),
            (
                (
                    "select platform_attachments.record_attachment_erasure_result_v64("
                    "%s,'partial','legacy','{}'::jsonb)"
                ),
                (uuid4(),),
            ),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                maintenance.execute(query, params)
            maintenance.rollback()
