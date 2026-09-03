from __future__ import annotations

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
import hashlib
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from app.attachments.artifact_service import (
    ArtifactOutputService,
    ArtifactUpload,
    ArtifactUploadConflict,
    BeginArtifactUpload,
)
from app.attachments.conversation_models import ObjectReceipt
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.control_plane.middleware import IdentitySecurityMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import _seed_task

TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
UPLOAD_ID = UUID("22222222-2222-4222-8222-222222222222")
ATTACHMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
ARTIFACT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
OWNER_ID = UUID("66666666-6666-4666-8666-666666666666")
CONVERSATION_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = datetime(2026, 9, 3, 10, tzinfo=UTC)
CONTENT = b"generated presentation"
DIGEST = hashlib.sha256(CONTENT).digest()


def _upload(*, state: str = "uploading", replayed: bool = False) -> ArtifactUpload:
    return ArtifactUpload(
        upload_id=UPLOAD_ID,
        attachment_id=ATTACHMENT_ID,
        artifact_id=ARTIFACT_ID,
        artifact_version_id=VERSION_ID,
        task_id=TASK_ID,
        agent_id="hr-bot",
        owner_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        artifact_key="candidate-deck",
        producer_version_id="producer-v1",
        display_name="candidate-deck.pptx",
        declared_mime=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        declared_size=len(CONTENT),
        expected_sha256=DIGEST,
        version_no=1,
        state=state,
        expires_at=NOW + timedelta(hours=1),
        replayed=replayed,
    )


class Repository:
    def __init__(self) -> None:
        self.calls = []
        self.current = _upload()

    def register(self, **values):
        self.calls.append(("register", values))
        return self.current

    def upload(self, **values):
        self.calls.append(("upload", values))
        return self.current

    def claim_write(self, **values):
        self.calls.append(("claim", values))
        return type(
            "Attempt",
            (),
            {
                "attempt_id": UUID("88888888-8888-4888-8888-888888888888"),
                "upload": self.current,
                "object_ref": "opaque-object-ref",
            },
        )()

    def abandon_write(self, **values):
        self.calls.append(("abandon", values))

    def finalize(self, **values):
        self.calls.append(("finalize", values))
        self.current = _upload(state="validating")
        return self.current


class Writer:
    def __init__(self, receipt: ObjectReceipt) -> None:
        self.receipt = receipt
        self.deleted = []

    def put_stream(self, object_ref, body, expected_size):
        assert object_ref == "opaque-object-ref"
        assert body.read() == CONTENT
        assert expected_size == len(CONTENT)
        return self.receipt

    def delete(self, object_ref):
        self.deleted.append(object_ref)


class WorkerAuth:
    route_prefix = "/"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None


class WorkerArtifactService:
    def __init__(self) -> None:
        self.calls = []

    def begin(self, token, task_id, request):
        self.calls.append(("begin", token, task_id, request))
        return _upload()

    def write(self, token, upload_id, body, content_length):
        self.calls.append(("write", token, upload_id, body.read(), content_length))
        return _upload(state="validating")

    def complete(self, token, upload_id):
        self.calls.append(("complete", token, upload_id))
        return _upload(state="validating")


def _worker_artifact_client():
    service = WorkerArtifactService()
    app = FastAPI()
    app.state.task_attachment_grant_service = object()
    app.state.artifact_output_service = service
    app.state.conversation_attachment_upload_service = object()
    app.state.conversation_attachment_download_service = object()
    app.include_router(build_conversation_attachment_router())
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=WorkerAuth(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
    )
    return TestClient(app), service


def _request() -> BeginArtifactUpload:
    return BeginArtifactUpload(
        agent_id="hr-bot",
        artifact_key="candidate-deck",
        producer_version_id="producer-v1",
        display_name="candidate-deck.pptx",
        declared_mime=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        declared_size=len(CONTENT),
        sha256_hex=DIGEST.hex(),
    )


def test_artifact_registration_is_idempotent_and_never_persists_raw_token() -> None:
    repository = Repository()
    service = ArtifactOutputService(repository, Writer(ObjectReceipt(len(CONTENT), DIGEST)))

    first = service.begin("c" * 43, TASK_ID, _request())
    repository.current = _upload(replayed=True)
    second = service.begin("c" * 43, TASK_ID, _request())

    assert first.artifact_id == second.artifact_id == ARTIFACT_ID
    assert first.version_no == second.version_no == 1
    assert second.replayed is True
    assert all(
        call[1]["token_sha256"] == hashlib.sha256(b"c" * 43).digest()
        for call in repository.calls
    )
    assert all("bearer_token" not in call[1] for call in repository.calls)


def test_artifact_write_verifies_declared_digest_before_finalize() -> None:
    repository = Repository()
    writer = Writer(ObjectReceipt(len(CONTENT), DIGEST))
    service = ArtifactOutputService(repository, writer)

    result = service.write("c" * 43, UPLOAD_ID, io.BytesIO(CONTENT), len(CONTENT))

    assert result.state == "validating"
    assert [name for name, _ in repository.calls] == ["upload", "claim", "finalize"]
    assert writer.deleted == []


def test_digest_mismatch_abandons_attempt_and_removes_written_object() -> None:
    repository = Repository()
    writer = Writer(ObjectReceipt(len(CONTENT), hashlib.sha256(b"wrong").digest()))
    service = ArtifactOutputService(repository, writer)

    with pytest.raises(ArtifactUploadConflict, match="integrity"):
        service.write("c" * 43, UPLOAD_ID, io.BytesIO(CONTENT), len(CONTENT))

    assert [name for name, _ in repository.calls] == ["upload", "claim", "abandon"]
    assert writer.deleted == ["opaque-object-ref"]


@pytest.mark.parametrize(
    "change",
    [
        {"agent_id": "wrong agent"},
        {"artifact_key": "../escape"},
        {"producer_version_id": ""},
        {"sha256_hex": "not-a-digest"},
    ],
)
def test_artifact_registration_rejects_malformed_contract(change) -> None:
    values = _request().__dict__ | change

    with pytest.raises(ValueError):
        BeginArtifactUpload(**values)


def test_worker_artifact_routes_are_bearer_only_strict_and_no_store() -> None:
    client, service = _worker_artifact_client()
    token = "d" * 43
    create_path = f"/api/v1/execution-worker/tasks/{TASK_ID}/artifacts"
    payload = {
        "agent_id": "hr-bot",
        "artifact_key": "candidate-deck",
        "producer_version_id": "producer-v1",
        "display_name": "candidate-deck.pptx",
        "declared_mime": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        "declared_size": len(CONTENT),
        "sha256": DIGEST.hex(),
    }

    assert client.post(create_path, json=payload).status_code == 401
    invalid = client.post(
        create_path,
        json={**payload, "unexpected": True},
        headers={"Authorization": "Bearer " + token},
    )
    assert invalid.status_code == 422
    created = client.post(
        create_path,
        json=payload,
        headers={"Authorization": "Bearer " + token},
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    assert token not in created.text
    assert created.json()["upload_id"] == str(UPLOAD_ID)
    assert created.json()["content_path"].endswith(f"/{UPLOAD_ID}/content")

    content_path = created.json()["content_path"]
    assert client.put(
        content_path,
        content=CONTENT,
        headers={
            "Authorization": "Bearer " + token,
            "Transfer-Encoding": "chunked",
        },
    ).status_code == 411
    written = client.put(
        content_path,
        content=CONTENT,
        headers={"Authorization": "Bearer " + token},
    )
    assert written.status_code == 200
    assert written.json()["state"] == "validating"

    completed = client.post(
        created.json()["complete_path"],
        headers={"Authorization": "Bearer " + token},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "validating"
    assert [call[0] for call in service.calls] == ["begin", "write", "complete"]


@pytest.mark.postgres
def test_output_registration_is_atomic_idempotent_and_charges_quota_once(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    token_sha256 = hashlib.sha256(b"output-token").digest()
    app_url = environment["urls"]["platform_control_app"]
    grant_id = UUID("99999999-9999-4999-8999-999999999999")
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,null,%s,'write_output',now()+interval '15 minutes',"
            "0,1024,2,512)",
            (grant_id, token_sha256, context["task_id"], context["agent_id"]),
        )
        first = app.execute(
            "select * from platform_attachments.create_artifact_upload_v64("
            "%s,%s,%s,%s,%s,%s,%s,'candidate-deck','producer-v1',"
            "%s,1,%s,1,'application/pdf',128,%s,now()+interval '1 hour')",
            (
                token_sha256,
                context["task_id"],
                context["agent_id"],
                UPLOAD_ID,
                ATTACHMENT_ID,
                ARTIFACT_ID,
                VERSION_ID,
                b"n" * 29,
                b"o" * 29,
                DIGEST,
            ),
        ).fetchone()
        second = app.execute(
            "select * from platform_attachments.create_artifact_upload_v64("
            "%s,%s,%s,%s,%s,%s,%s,'candidate-deck','producer-v1',"
            "%s,1,%s,1,'application/pdf',128,%s,now()+interval '1 hour')",
            (
                token_sha256,
                context["task_id"],
                context["agent_id"],
                UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                b"n" * 29,
                b"o" * 29,
                DIGEST,
            ),
        ).fetchone()
        app.commit()

    assert first[:5] == (UPLOAD_ID, ATTACHMENT_ID, ARTIFACT_ID, VERSION_ID, 1)
    assert first[5] is False
    assert second[:5] == first[:5]
    assert second[5] is True
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select file_count,bytes_read from platform_attachments.task_grants "
            "where grant_id=%s",
            (grant_id,),
        ).fetchone() == (1, 128)
        assert admin.execute(
            "select source_kind,state from platform_attachments.attachments "
            "where attachment_id=%s",
            (ATTACHMENT_ID,),
        ).fetchone() == ("agent_output", "uploading")
        assert admin.execute(
            "select kind,task_id,agent_id from platform_attachments.bindings "
            "where attachment_id=%s",
            (ATTACHMENT_ID,),
        ).fetchone() == ("task_output", context["task_id"], context["agent_id"])


@pytest.mark.postgres
def test_output_finalize_rejects_digest_mismatch_and_starts_shared_pipeline(
    control_database,
) -> None:
    upload_id = UUID("12121212-1212-4212-8212-121212121212")
    attachment_id = UUID("13131313-1313-4313-8313-131313131313")
    artifact_id = UUID("14141414-1414-4414-8414-141414141414")
    version_id = UUID("15151515-1515-4515-8515-151515151515")
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
    token_sha256 = hashlib.sha256(b"finalize-token").digest()
    app_url = environment["urls"]["platform_control_app"]
    grant_id = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,null,%s,'write_output',now()+interval '15 minutes',"
            "0,1024,2,512)",
            (grant_id, token_sha256, context["task_id"], context["agent_id"]),
        )
        app.execute(
            "select * from platform_attachments.create_artifact_upload_v64("
            "%s,%s,%s,%s,%s,%s,%s,'candidate-deck','producer-v1',"
            "%s,1,%s,1,'application/pdf',128,%s,now()+interval '1 hour')",
            (
                token_sha256,
                context["task_id"],
                context["agent_id"],
                upload_id,
                attachment_id,
                artifact_id,
                version_id,
                b"n" * 29,
                b"o" * 29,
                DIGEST,
            ),
        )
        attempt_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
        app.execute(
            "select platform_attachments.claim_artifact_upload_write_v64("
            "%s,%s,%s,%s,1,now()+interval '5 minutes')",
            (token_sha256, upload_id, attempt_id, b"w" * 29),
        )
        app.commit()

    with (
        psycopg.connect(app_url) as app,
        pytest.raises(psycopg.errors.CheckViolation, match="digest"),
    ):
        app.execute(
            "select platform_attachments.finalize_artifact_upload_v64("
            "%s,%s,%s,'application/pdf',128,%s)",
            (token_sha256, upload_id, attempt_id, hashlib.sha256(b"wrong").digest()),
        )

    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_attachments.finalize_artifact_upload_v64("
            "%s,%s,%s,'application/pdf',128,%s)",
            (token_sha256, upload_id, attempt_id, DIGEST),
        ).fetchone() == (attachment_id,)
        app.commit()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state from platform_attachments.attachments where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("validating",)
        assert admin.execute(
            "select state,result_status from platform_attachments.artifact_versions "
            "where artifact_version_id=%s",
            (version_id,),
        ).fetchone() == ("validating", "pending")
        assert admin.execute(
            "select job_kind,state from platform_attachments.processing_jobs "
            "where attachment_id=%s",
            (attachment_id,),
        ).fetchone() == ("validate", "queued")
