from __future__ import annotations

# Pytest fixture imports intentionally enter this module's namespace.
# ruff: noqa: F401,F811
from uuid import uuid4

import psycopg
import pytest
from app.attachments.conversation_repository import attachment_object_subject
from app.attachments.erasure import (
    AttachmentErasureRepository,
    AttachmentErasureService,
)
from app.attachments.object_writer import AttachmentObjectWriter
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import _insert_attachment, _seed_task


def _codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(
            active_version=7,
            purpose="platform-content-encryption",
            _keys={7: b"7" * 32},
        )
    )


def _sealed_ref(codec: ContentCodec, subject: str, object_ref: str):
    sealed = codec.seal_json(subject, {"object_ref": object_ref})
    return sealed.ciphertext, sealed.key_version


def _insert_erasure_job(connection, context, attachment_id):
    job_id = uuid4()
    connection.execute(
        "insert into platform_attachments.erasure_jobs ("
        "erasure_job_id,attachment_id,requested_by_internal_user_id,"
        "reason_ciphertext,reason_key_version,reason_sha256) "
        "values (%s,%s,%s,%s,1,%s)",
        (job_id, attachment_id, context["owner_id"], b"e" * 29, b"e" * 32),
    )
    connection.commit()
    return job_id


def _seed_object_graph(connection, codec, context):
    attachment_id = _insert_attachment(connection, context)
    upload_id = uuid4()
    canonical_attempt_id = uuid4()
    stale_attempt_id = uuid4()
    derivative_id = uuid4()
    refs = {
        "objects/original",
        "objects/canonical-attempt",
        "objects/stale-attempt",
        "objects/text-derivative",
    }
    original = _sealed_ref(
        codec,
        attachment_object_subject(attachment_id, canonical_attempt_id),
        "objects/original",
    )
    canonical = _sealed_ref(
        codec,
        attachment_object_subject(attachment_id, canonical_attempt_id),
        "objects/canonical-attempt",
    )
    stale = _sealed_ref(
        codec,
        attachment_object_subject(attachment_id, stale_attempt_id),
        "objects/stale-attempt",
    )
    derivative = _sealed_ref(
        codec,
        f"attachment:{attachment_id}:derivative:{derivative_id}:object-ref",
        "objects/text-derivative",
    )
    connection.execute(
        "update platform_attachments.attachments set "
        "object_ref_ciphertext=%s,object_ref_key_version=%s where attachment_id=%s",
        (*original, attachment_id),
    )
    connection.execute(
        "insert into platform_attachments.uploads ("
        "upload_id,attachment_id,owner_internal_user_id,conversation_id,"
        "object_ref_ciphertext,object_ref_key_version,state,write_attempt_id,"
        "write_lease_expires_at) values (%s,%s,%s,%s,%s,%s,'ready',%s,now())",
        (
            upload_id,
            attachment_id,
            context["owner_id"],
            context["conversation_id"],
            *canonical,
            canonical_attempt_id,
        ),
    )
    for attempt_id, sealed, state in (
        (canonical_attempt_id, canonical, "canonical"),
        (stale_attempt_id, stale, "superseded"),
    ):
        connection.execute(
            "insert into platform_attachments.upload_write_attempts ("
            "attempt_id,upload_id,attachment_id,owner_internal_user_id,"
            "object_ref_ciphertext,object_ref_key_version,lease_expires_at,state) "
            "values (%s,%s,%s,%s,%s,%s,now(),%s)",
            (
                attempt_id,
                upload_id,
                attachment_id,
                context["owner_id"],
                *sealed,
                state,
            ),
        )
    connection.execute(
        "insert into platform_attachments.derivatives ("
        "derivative_id,attachment_id,kind,object_ref_ciphertext,"
        "object_ref_key_version,sha256,state) values (%s,%s,'text',%s,%s,%s,'ready')",
        (derivative_id, attachment_id, *derivative, b"d" * 32),
    )
    job_id = _insert_erasure_job(connection, context, attachment_id)
    return attachment_id, job_id, refs


class _ObjectClient:
    def __init__(self, refs):
        self.refs = set(refs)
        self.deleted = []

    def delete_object(self, *, Bucket, Key):
        assert Bucket == "attachment-regression"
        assert Key in self.refs
        self.refs.remove(Key)
        self.deleted.append(Key)


@pytest.mark.postgres
def test_legacy_composite_single_claim_loses_attachment_id(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        job_id = _insert_erasure_job(admin, context, attachment_id)
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as maintenance:
        row = maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('legacy-single')).*"
        ).fetchone()
        maintenance.commit()
    assert row is not None
    assert row[0] == job_id
    assert row[1] is None


@pytest.mark.postgres
def test_legacy_composite_multi_claim_splices_two_jobs(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        first_attachment = _insert_attachment(admin, context)
        second_attachment = _insert_attachment(admin, context)
        expected = {
            _insert_erasure_job(admin, context, first_attachment): first_attachment,
            _insert_erasure_job(admin, context, second_attachment): second_attachment,
        }
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as maintenance:
        row = maintenance.execute(
            "select (platform_attachments."
            "claim_attachment_erasure_job_v64('legacy-multiple')).*"
        ).fetchone()
        maintenance.commit()
    assert row is not None
    assert row[0] in expected
    assert row[1] in expected.values()
    assert expected[row[0]] != row[1]
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_attachments.erasure_jobs "
            "where state='running' and claimed_by='legacy-multiple'"
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_fixed_worker_claims_one_job_and_deletes_every_object(control_database):
    environment = control_database["environments"]["production"]
    codec = _codec()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        _attachment_id, job_id, refs = _seed_object_graph(admin, codec, context)
        untouched_attachment = _insert_attachment(admin, context)
        untouched_job = _insert_erasure_job(admin, context, untouched_attachment)
    client = _ObjectClient(refs)
    repository = AttachmentErasureRepository(
        environment["urls"]["platform_control_maintenance"], content_codec=codec
    )
    service = AttachmentErasureService(
        repository, AttachmentObjectWriter(client, "attachment-regression")
    )

    assert service.process_next("fixed-worker") is True
    assert set(client.deleted) == refs
    assert client.refs == set()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select erasure.state,attachment.state,"
            "erasure.downstream_cleanup_status from platform_attachments.erasure_jobs erasure "
            "join platform_attachments.attachments attachment using (attachment_id) "
            "where erasure.erasure_job_id=%s",
            (job_id,),
        ).fetchone() == (
            "completed",
            "deleted",
            {"deleted_count": 4, "failed_count": 0, "object_count": 4},
        )
        assert admin.execute(
            "select state,claimed_by,attempt_count from platform_attachments.erasure_jobs "
            "where erasure_job_id=%s",
            (untouched_job,),
        ).fetchone() == ("queued", None, 0)


@pytest.mark.postgres
def test_migration_100_grants_only_required_maintenance_columns(control_database):
    for environment in control_database["environments"].values():
        maintenance_role = environment["roles"][5]
        with psycopg.connect(environment["admin"]) as admin:
            assert admin.execute(
                "select version from platform_control.schema_migrations where version=100"
            ).fetchone() == (100,)
            allowed = {
                ("uploads", "attachment_id"),
                ("uploads", "write_attempt_id"),
                ("upload_write_attempts", "attachment_id"),
                ("upload_write_attempts", "attempt_id"),
                ("upload_write_attempts", "object_ref_ciphertext"),
                ("upload_write_attempts", "object_ref_key_version"),
            }
            actual = {
                (table, column)
                for table, column in admin.execute(
                    "select table_name,column_name from information_schema.columns "
                    "where table_schema='platform_attachments' and table_name in "
                    "('uploads','upload_write_attempts') and "
                    "has_column_privilege(%s,quote_ident(table_schema)||'.'||"
                    "quote_ident(table_name),column_name,'SELECT')",
                    (maintenance_role,),
                )
            }
            assert actual == allowed
