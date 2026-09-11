"""Current authorization over real uploads, independent of object body reads."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from app.hr_agent.types import HrAgentProblem, content_sha256
from tests import test_hr_agent_materials as fixtures

uploaded = fixtures.uploaded
secured = fixtures.secured
database = fixtures.database
_FIXTURES = (uploaded, secured, database)


def expected_ref(aid, data):
    digest = hashlib.sha256(data).hexdigest()
    original = {
        "kind": "material",
        "id": aid + ":original",
        "revision": digest,
        "sha256": content_sha256(
            {
                "byte_sha256": digest,
                "detected_mime": "text/plain",
                "size_bytes": len(data),
            }
        ),
    }
    return {
        "kind": "material",
        "id": aid + ":text",
        "revision": digest + ":utf8-v1",
        "sha256": content_sha256(
            {
                "original_ref": original,
                "parser_release": "utf8-v1",
                "text": data.decode(),
                "coverage_complete": True,
            }
        ),
    }


def test_missing_proof_requires_actual_resolution_and_cannot_be_submitted(uploaded):
    client, headers, _, owner, materials, aid, data, _ = uploaded
    ref = expected_ref(aid, data)
    with pytest.raises(HrAgentProblem) as error:
        materials.authorize_refs(owner, [ref])
    assert error.value.http_status == 410
    response = client.post(
        "/api/hr/agent/works",
        json={**fixtures.body(), "references": [ref]},
        headers=headers,
    )
    assert response.status_code == 410, response.text
    # No HTTP endpoint accepts an authority record supplied by a caller.
    response = client.post(
        f"/api/hr/agent/materials/{aid}/authority", json={"ref": ref}, headers=headers
    )
    assert response.status_code == 403
    with pytest.raises(HrAgentProblem):
        materials.authorize_refs(owner, [ref])
    assert client.get("/api/hr/agent/materials/" + aid).json()["text_ref"] == ref
    materials.authorize_refs(owner, [ref])


def test_batch_authorization_uses_one_connection_without_objects_or_crypto(
    uploaded, database, monkeypatch
):
    client, _, _, owner, materials, aid, _, store = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    from tests.test_hr_agent_material_parsing import upload_document

    refs = [ref]
    for index in range(2):
        another = upload_document(
            uploaded,
            database,
            f"Another public JD {index}".encode(),
            "text/plain",
            f"public-{index}.txt",
        )
        refs.append(client.get("/api/hr/agent/materials/" + another).json()["text_ref"])
    calls = []
    original = materials.connection_factory

    @contextmanager
    def counted():
        calls.append(1)
        with original() as connection:
            yield connection

    def forbidden(*args, **kwargs):
        pytest.fail("authorization must not read/decrypt object or text")

    monkeypatch.setattr(materials, "connection_factory", counted)
    monkeypatch.setattr(store, "read_verified", forbidden)
    materials.codec = SimpleNamespace(unseal_json=forbidden)
    materials.authorize_refs(owner, refs * 40)
    assert len(calls) == 1
    materials.authorize_refs(owner, [ref])
    assert len(calls) == 2, "authorization decisions must not be cached across calls"


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "quarantine",
        "deleted",
        "erasure",
        "sha",
        "mime",
        "size",
        "locator",
        "write_attempt",
    ],
)
def test_current_metadata_revocation_overrides_verified_proof(
    uploaded, database, change
):
    client, _, _, owner, materials, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    materials.authorize_refs(owner, [ref])
    updates = {
        "expiry": "retained_until=now()-interval '1 second'",
        "quarantine": "state='quarantined'",
        "deleted": "state='deleted',deleted_at=now()",
        "sha": "sha256=decode(repeat('00',32),'hex')",
        "mime": "detected_mime='application/pdf'",
        "size": "size_bytes=size_bytes+1",
        "locator": "immutable_locator='etag:other-immutable'",
    }
    with database.admin_connection() as connection:
        if change in updates:
            connection.execute(
                "UPDATE platform_attachments.attachments SET "
                + updates[change]
                + " WHERE attachment_id=%s",
                (aid,),
            )
        elif change == "write_attempt":
            connection.execute(
                "UPDATE platform_attachments.uploads SET write_attempt_id=%s WHERE attachment_id=%s",
                (uuid4(), aid),
            )
        else:
            connection.execute(
                "INSERT INTO platform_attachments.erasure_jobs(erasure_job_id,attachment_id,requested_by_internal_user_id,reason_ciphertext,reason_key_version,reason_sha256) VALUES(%s,%s,%s,%s,1,%s)",
                (uuid4(), aid, owner, b"x" * 29, b"x" * 32),
            )
    with pytest.raises(HrAgentProblem) as error:
        materials.authorize_refs(owner, [ref])
    assert error.value.http_status == 410


def test_wrong_owner_revision_hash_kind_and_unknown_attachment_rejected(uploaded):
    client, _, _, owner, materials, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    for actor, selected in [
        (uuid4(), ref),
        (owner, {**ref, "revision": ref["revision"] + "wrong"}),
        (owner, {**ref, "sha256": "0" * 64}),
        (owner, {**ref, "id": str(uuid4()) + ":text"}),
        (owner, {**ref, "id": aid + ":original"}),
        (owner, {**ref, "kind": "result"}),
    ]:
        with pytest.raises(HrAgentProblem):
            materials.authorize_refs(actor, [selected])


def test_verified_proof_is_append_only_and_contains_no_body_or_locator(
    uploaded, database
):
    client, _, _, owner, materials, aid, data, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    materials.read_text(owner, ref)
    with database.connection() as connection:
        row = connection.execute(
            "SELECT * FROM platform_hr_agent.material_authority_proofs WHERE owner_id=%s AND attachment_id=%s",
            (owner, aid),
        ).fetchall()
        assert len(row) == 1
        assert data not in repr(row).encode()
        assert b"local-immutable" not in repr(row).encode()
        columns = {
            r[0]
            for r in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='platform_hr_agent' AND table_name='material_authority_proofs'"
            )
        }
        assert columns == {
            "owner_id",
            "attachment_id",
            "text_revision",
            "text_sha256",
            "source_identity_sha",
            "created_at",
        }
        for permission in ("UPDATE", "DELETE"):
            assert not connection.execute(
                "SELECT has_table_privilege(current_user,'platform_hr_agent.material_authority_proofs',%s)",
                (permission,),
            ).fetchone()[0]
    with (
        database.connection() as connection,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        connection.execute(
            "INSERT INTO platform_hr_agent.material_authority_proofs SELECT %s,attachment_id,text_revision,text_sha256,source_identity_sha,created_at FROM platform_hr_agent.material_authority_proofs WHERE attachment_id=%s",
            (uuid4(), aid),
        )


def test_storage_tamper_still_fails_explicit_read_without_calling_it_an_auth_audit(
    uploaded,
):
    client, _, _, owner, materials, aid, _, store = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    store.objects[next(iter(store.objects))] = b"tampered bytes"
    # Metadata remains unchanged: immutable-store integrity is a separate boundary.
    materials.authorize_refs(owner, [ref])
    with pytest.raises(HrAgentProblem) as error:
        materials.read_text(owner, ref)
    assert error.value.http_status == 503


def test_missing_receipt_can_be_reestablished_only_by_matching_verified_read(
    uploaded, database
):
    client, _, _, owner, materials, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    with database.admin_connection() as connection:
        connection.execute(
            "DELETE FROM platform_hr_agent.material_authority_proofs WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem):
        materials.authorize_refs(owner, [ref])
    with pytest.raises(HrAgentProblem):
        materials.read_text(owner, {**ref, "sha256": "0" * 64})
    with pytest.raises(HrAgentProblem):
        materials.authorize_refs(owner, [ref])
    materials.read_text(owner, ref)
    materials.authorize_refs(owner, [ref])


def test_attachment_physical_delete_cascades_proof(uploaded, database):
    client, _, _, owner, materials, aid, _, _ = uploaded
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    with database.admin_connection() as connection:
        # Remove only this test upload's dependent processing/audit rows so the
        # real attachment DELETE can exercise the authority FK's cascade.
        for table in (
            "access_events",
            "processing_jobs",
            "derivatives",
            "upload_write_attempts",
            "uploads",
        ):
            from psycopg import sql

            connection.execute(
                sql.SQL(
                    "DELETE FROM platform_attachments.{} WHERE attachment_id=%s"
                ).format(sql.Identifier(table)),
                (aid,),
            )
        connection.execute(
            "DELETE FROM platform_attachments.attachments WHERE attachment_id=%s",
            (aid,),
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM platform_hr_agent.material_authority_proofs WHERE attachment_id=%s",
                (aid,),
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(HrAgentProblem):
        materials.authorize_refs(owner, [ref])


@pytest.mark.parametrize("damage", ["original", "parsed_ciphertext"])
def test_parsed_authorization_does_not_decrypt_but_explicit_read_checks_integrity(
    uploaded, database, monkeypatch, damage
):
    from app.hr_agent.material_parsing import MaterialParsingService
    from tests.test_hr_agent_material_parsing import DOCX, docx, upload_document

    client, headers, repo, owner, materials, _, _, store = uploaded
    parser = MaterialParsingService(repo, materials)
    aid = upload_document(uploaded, database, docx(), DOCX, "public.docx")
    response = client.post(
        f"/api/hr/agent/materials/{aid}/parse", headers=headers, json={}
    )
    assert response.status_code == 202, response.text
    assert parser.process_one("authority-parser")
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    assert ref is not None
    with monkeypatch.context() as patch:

        def forbidden(*args, **kwargs):
            pytest.fail("parsed authority must not read source or decrypt parsed body")

        patch.setattr(store, "read_verified", forbidden)
        patch.setattr(repo, "_unseal", forbidden)
        materials.authorize_refs(owner, [ref])
    if damage == "original":
        for name, value in list(store.objects.items()):
            if value.startswith(b"PK"):
                store.objects[name] = b"tampered original"
    else:
        with database.admin_connection() as connection:
            connection.execute(
                "UPDATE platform_hr_agent.material_parses SET sealed_content=decode(repeat('00',40),'hex') WHERE attachment_id=%s",
                (aid,),
            )
    materials.authorize_refs(owner, [ref])
    with pytest.raises(HrAgentProblem) as error:
        materials.read_text(owner, ref)
    assert error.value.http_status == 503


def test_same_source_hash_with_new_locator_cannot_reuse_parsed_proof(
    uploaded, database
):
    from app.hr_agent.material_parsing import MaterialParsingService
    from tests.test_hr_agent_material_parsing import DOCX, docx, upload_document

    client, headers, repo, owner, materials, _, _, _ = uploaded
    parser = MaterialParsingService(repo, materials)
    aid = upload_document(uploaded, database, docx(), DOCX, "public.docx")
    assert (
        client.post(
            f"/api/hr/agent/materials/{aid}/parse", headers=headers, json={}
        ).status_code
        == 202
    )
    assert parser.process_one("authority-parser")
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    with database.admin_connection() as connection:
        connection.execute(
            "UPDATE platform_attachments.attachments SET immutable_locator='etag:same-hash-new-source' WHERE attachment_id=%s",
            (aid,),
        )
    for action in (
        lambda: materials.authorize_refs(owner, [ref]),
        lambda: materials.resolve(owner, aid),
    ):
        with pytest.raises(HrAgentProblem) as error:
            action()
        assert error.value.http_status == 410


def test_resource_scope_keeps_current_selection_and_batches_materials(
    uploaded, database, monkeypatch
):
    from app.hr_agent.resources import ResourceReader
    from tests.test_hr_agent_material_parsing import upload_document

    client, headers, repo, owner, materials, aid, _, _ = uploaded
    first = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    another = upload_document(
        uploaded, database, b"Another public role", "text/plain", "another.txt"
    )
    second = client.get("/api/hr/agent/materials/" + another).json()["text_ref"]
    response = client.post(
        "/api/hr/agent/works",
        json={**fixtures.body(), "references": [first]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    reader = ResourceReader(repo, None, material_service=materials)
    calls = []
    original = materials.authorize_refs

    def counted(actor, refs):
        calls.append(list(refs))
        return original(actor, refs)

    monkeypatch.setattr(materials, "authorize_refs", counted)
    reader.validate_scope(owner, [], [first, second])
    assert calls == [[first, second]]
    with pytest.raises(HrAgentProblem) as error:
        reader.validate_scope(owner, [], [second], response.json()["work_id"])
    assert error.value.http_status == 403
