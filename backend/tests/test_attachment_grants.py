from __future__ import annotations

# Imported fixture names intentionally become pytest fixtures in this module.
# ruff: noqa: F401,F811
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.download_service import DownloadAsset
from app.attachments.grant_service import (
    AttachmentGrantService,
    OutputWriteGrant,
    TaskAttachmentGrant,
    TaskGrantRepository,
    TaskGrantUnavailable,
)
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.execution_relay.content_crypto import ContentCodec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database
from test_conversation_attachment_migration import (
    _insert_attachment,
    _insert_artifact,
    _insert_task_input_binding,
    _insert_task_output_binding,
    _seed_task,
)

TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
ATTACHMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
OWNER_ID = UUID("33333333-3333-4333-8333-333333333333")
CONVERSATION_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 9, 3, 10, tzinfo=UTC)
PAYLOAD = b"candidate evidence"


def _asset() -> DownloadAsset:
    return DownloadAsset(
        ATTACHMENT_ID,
        OWNER_ID,
        CONVERSATION_ID,
        "candidate.pdf",
        "application/pdf",
        len(PAYLOAD),
        hashlib.sha256(PAYLOAD).digest(),
        "ready",
        "opaque-object-ref",
        "version:immutable-v1",
    )


class GrantRepository:
    def __init__(self) -> None:
        self.issued = []
        self.consumed = []
        self.classified = []

    def issue_read(self, **values):
        self.issued.append(("read", values))
        return _asset()

    def issue_output(self, **values):
        self.issued.append(("write_output", values))

    def consume_read(self, **values):
        self.consumed.append(values)
        return _asset()

    def classify_result_artifacts(self, **values):
        self.classified.append(values)
        return "ready"


class Store:
    def stage_verified(self, asset, directory):
        assert asset == _asset()
        target = Path(directory) / "content"
        target.write_bytes(PAYLOAD)
        return target


class WorkerAuth:
    route_prefix = "/"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None


class WorkerGrantService:
    def __init__(self) -> None:
        self.calls = []

    def open_attachment(self, token, attachment_id):
        self.calls.append((token, attachment_id))
        return type(
            "Opened",
            (),
            {
                "stream": iter((PAYLOAD,)),
                "status_code": 200,
                "media_type": "application/pdf",
                "headers": {"Content-Length": str(len(PAYLOAD))},
            },
        )()


def _worker_grant_client():
    service = WorkerGrantService()
    app = FastAPI()
    app.state.task_attachment_grant_service = service
    app.state.artifact_output_service = object()
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


def test_grants_return_raw_capability_once_but_persist_only_its_digest() -> None:
    repository = GrantRepository()
    service = AttachmentGrantService(
        repository,
        Store(),
        clock=lambda: NOW,
        token_factory=lambda: "a" * 43,
    )

    read = service.issue_attachment(TASK_ID, ATTACHMENT_ID, "hr-bot")
    output = service.issue_output(TASK_ID, "hr-bot")

    assert isinstance(read, TaskAttachmentGrant)
    assert read.bearer_token == "a" * 43
    assert read.download_url == (
        f"/api/v1/execution-worker/attachments/{ATTACHMENT_ID}/content"
    )
    assert read.sha256_hex == hashlib.sha256(PAYLOAD).hexdigest()
    assert isinstance(output, OutputWriteGrant)
    assert output.bearer_token == "a" * 43
    assert output.upload_url == (
        f"/api/v1/execution-worker/tasks/{TASK_ID}/artifacts"
    )
    assert all(
        call[1]["token_sha256"] == hashlib.sha256(b"a" * 43).digest()
        for call in repository.issued
    )
    assert all("bearer_token" not in call[1] for call in repository.issued)
    assert "a" * 43 not in repr(read)
    assert "a" * 43 not in repr(output)


def test_media_gateway_consumes_full_byte_budget_before_streaming() -> None:
    repository = GrantRepository()
    service = AttachmentGrantService(repository, Store())

    opened = service.open_attachment("b" * 43, ATTACHMENT_ID)

    assert b"".join(opened.stream) == PAYLOAD
    assert opened.media_type == "application/pdf"
    assert opened.headers["Content-Length"] == str(len(PAYLOAD))
    assert opened.headers["Cache-Control"] == "no-store"
    assert repository.consumed == [
        {
            "token_sha256": hashlib.sha256(b"b" * 43).digest(),
            "attachment_id": ATTACHMENT_ID,
        }
    ]


@pytest.mark.parametrize("token", ["", "short", "a" * 42, "a" * 44, "!" * 43])
def test_media_gateway_rejects_malformed_bearer_without_repository_access(
    token: str,
) -> None:
    repository = GrantRepository()
    service = AttachmentGrantService(repository, Store())

    with pytest.raises(TaskGrantUnavailable):
        service.open_attachment(token, ATTACHMENT_ID)

    assert repository.consumed == []


def test_grant_lifetime_and_quotas_cannot_exceed_product_caps() -> None:
    repository = GrantRepository()
    service = AttachmentGrantService(repository, Store(), clock=lambda: NOW)

    with pytest.raises(ValueError):
        service.issue_attachment(
            TASK_ID,
            ATTACHMENT_ID,
            "hr-bot",
            expires_at=NOW + timedelta(hours=25),
        )
    with pytest.raises(ValueError):
        service.issue_output(TASK_ID, "hr-bot", max_files=21)


def test_result_artifact_classification_is_task_and_agent_bound() -> None:
    repository = GrantRepository()
    service = AttachmentGrantService(repository, Store())
    artifacts = (
        {
            "attachmentId": str(ATTACHMENT_ID),
            "artifactKey": "candidate-report",
            "producerVersionId": "report-v1",
            "displayName": "candidate-report.pdf",
            "status": "ready",
        },
    )

    assert service.classify_result_artifacts(TASK_ID, "hr-bot", artifacts) == "ready"
    assert repository.classified == [
        {"task_id": TASK_ID, "agent_id": "hr-bot", "artifacts": artifacts}
    ]


@pytest.mark.postgres
def test_result_artifact_classification_requires_registered_ready_version(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin, agent_id="hr-bot")
        artifact_id = _insert_artifact(admin, context, "candidate-report")
        attachment_id = _insert_attachment(
            admin, context, source_kind="agent_output"
        )
        _insert_task_output_binding(admin, context, attachment_id)
        admin.execute(
            "update platform_attachments.attachments set "
            "immutable_locator='version:artifact-v1' where attachment_id=%s",
            (attachment_id,),
        )
        admin.execute(
            "insert into platform_attachments.artifact_versions("
            "artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,size_bytes,state,result_status) "
            "values (%s,%s,%s,1,'report-v1',%s,1,%s,1,128,'ready','succeeded')",
            (uuid4(), artifact_id, attachment_id, b"n" * 29, b"r" * 29),
        )
        admin.commit()

    codec = ContentCodec(
        IdentityKeyring(
            active_version=1,
            purpose="platform-content-encryption",
            _keys={1: b"k" * 32},
        )
    )
    service = AttachmentGrantService(
        TaskGrantRepository(
            environment["urls"]["platform_control_app"], content_codec=codec
        ),
        Store(),
    )
    artifacts = (
        {
            "attachmentId": str(attachment_id),
            "artifactKey": "candidate-report",
            "producerVersionId": "report-v1",
            "displayName": "candidate-report.pdf",
            "status": "ready",
        },
    )

    assert (
        service.classify_result_artifacts(
            context["task_id"], "hr-bot", artifacts
        )
        == "ready"
    )
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_attachments.artifact_versions set "
            "state='scanning',result_status='pending' where attachment_id=%s",
            (attachment_id,),
        )
        admin.commit()
    assert (
        service.classify_result_artifacts(
            context["task_id"], "hr-bot", artifacts
        )
        == "pending"
    )


def test_worker_media_gateway_requires_header_bearer_and_never_caches() -> None:
    client, service = _worker_grant_client()
    path = f"/api/v1/execution-worker/attachments/{ATTACHMENT_ID}/content"

    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "a" * 43}).status_code == 404
    response = client.get(path, headers={"Authorization": "Bearer " + "a" * 43})

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == [("a" * 43, ATTACHMENT_ID)]


@pytest.mark.postgres
def test_gateway_consume_rechecks_task_binding_state_expiry_budget_and_revocation(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    token_sha256 = hashlib.sha256(b"gateway-token").digest()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        _insert_task_input_binding(admin, context, attachment_id)

    grant_id = UUID("99999999-9999-4999-8999-999999999999")
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,%s,%s,'read',now()+interval '15 minutes',2,256)",
            (
                grant_id,
                token_sha256,
                context["task_id"],
                attachment_id,
                context["agent_id"],
            ),
        )
        assert app.execute(
            "select platform_attachments.consume_task_grant_gateway_v64(%s,%s,128)",
            (token_sha256, attachment_id),
        ).fetchone() == (grant_id,)
        app.commit()

    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select read_count,bytes_read from platform_attachments.task_grants "
            "where grant_id=%s",
            (grant_id,),
        ).fetchone() == (1, 128)

    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_attachments.revoke_task_grant_v64(%s)", (grant_id,)
        )
        app.commit()
    with (
        psycopg.connect(app_url) as app,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="unavailable"),
    ):
        app.execute(
            "select platform_attachments.consume_task_grant_gateway_v64(%s,%s,128)",
            (token_sha256, attachment_id),
        )


@pytest.mark.postgres
def test_gateway_consume_rejects_terminal_task_even_with_unexpired_token(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    token_sha256 = hashlib.sha256(b"terminal-token").digest()
    with psycopg.connect(environment["admin"]) as admin:
        context = _seed_task(admin)
        attachment_id = _insert_attachment(admin, context)
        _insert_task_input_binding(admin, context, attachment_id)
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        app.execute(
            "select platform_attachments.issue_task_grant_v64("
            "%s,%s,%s,%s,%s,'read',now()+interval '15 minutes',2,256)",
            (
                UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
                token_sha256,
                context["task_id"],
                attachment_id,
                context["agent_id"],
            ),
        )
        app.commit()
    with psycopg.connect(environment["admin"]) as admin:
        admin.execute(
            "update platform_control.mission_tasks set status='completed',terminal_at=now() "
            "where task_id=%s",
            (context["task_id"],),
        )
        admin.commit()
    with (
        psycopg.connect(app_url) as app,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="terminal"),
    ):
        app.execute(
            "select platform_attachments.consume_task_grant_gateway_v64(%s,%s,128)",
            (token_sha256, attachment_id),
        )
