from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from app.attachments.conversation_models import AttachmentRecord, UploadRecord
from app.attachments.conversation_repository import ConversationAttachmentRepository
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
    DownloadAsset,
    DownloadNotFound,
    DownloadRangeError,
    S3ImmutableAttachmentStore,
)
from app.control_plane.authorization import AuthorizationService
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.execution_relay.content_crypto import ContentCodec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database  # noqa: F401

OWNER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CONVERSATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ATTACHMENT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
UPLOAD_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


def attachment(*, state="ready", name="候选人报告.pdf", mime="application/pdf"):
    return AttachmentRecord(
        ATTACHMENT_ID,
        OWNER_ID,
        CONVERSATION_ID,
        name,
        mime,
        mime,
        7,
        hashlib.sha256(b"payload").digest(),
        state,
        NOW,
        NOW + timedelta(days=365),
    )


def upload(*, state="uploading"):
    return UploadRecord(
        UPLOAD_ID,
        ATTACHMENT_ID,
        OWNER_ID,
        CONVERSATION_ID,
        "候选人报告.pdf",
        "application/pdf",
        7,
        NOW + timedelta(hours=1),
        state,
        None if state == "uploading" else 7,
        None if state == "uploading" else hashlib.sha256(b"payload").digest(),
    )


class FakeUploadService:
    def __init__(self):
        self.calls = []

    def begin(self, owner_id, request):
        self.calls.append(("begin", owner_id, request))
        return upload()

    def write(self, owner_id, upload_id, body, content_length):
        self.calls.append(("write", owner_id, upload_id, body.read(), content_length))
        return upload(state="validating")

    def complete(self, owner_id, upload_id):
        self.calls.append(("complete", owner_id, upload_id))
        return attachment(state="validating")


class FakeDownloadService:
    def __init__(self):
        self.calls = []

    def cancel_upload(self, owner_id, upload_id):
        self.calls.append(("cancel", owner_id, upload_id))

    def attachment(self, owner_id, attachment_id):
        self.calls.append(("attachment", owner_id, attachment_id))
        return attachment()

    def list_conversation(self, owner_id, conversation_id):
        self.calls.append(("list", owner_id, conversation_id))
        return (attachment(),)

    def issue_ticket(self, owner_id, attachment_id, purpose):
        self.calls.append(("ticket", owner_id, attachment_id, purpose))
        return SimpleNamespace(
            ticket="opaque-ticket",
            expires_at=NOW + timedelta(seconds=300),
            content_path="/api/v1/attachments/content/opaque-ticket",
        )

    def open_content(self, owner_id, ticket, range_header):
        self.calls.append(("content", owner_id, ticket, range_header))
        return SimpleNamespace(
            stream=iter((b"payload",)),
            status_code=200,
            media_type="application/pdf",
            headers={"Content-Length": "7", "Content-Disposition": "attachment"},
        )

    def delete_attachment(self, owner_id, attachment_id):
        self.calls.append(("delete", owner_id, attachment_id))

    def archive_conversation(self, owner_id, conversation_id):
        self.calls.append(("archive", owner_id, conversation_id))
        return SimpleNamespace(
            stream=iter((b"zip",)),
            status_code=200,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment"},
        )


class Auth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None

    def authenticate(self, token):
        users = {"owner": OWNER_ID, "other": OTHER_ID}
        return (
            (AuthContext(users[token], Role.MEMBER, uuid4(), False), b"csrf")
            if token in users
            else None
        )

    def verify_csrf(self, token, digest):
        return token == "csrf-token" and digest == b"csrf"


def api_client():
    uploads, downloads = FakeUploadService(), FakeDownloadService()
    app = FastAPI()
    app.state.conversation_attachment_upload_service = uploads
    app.state.conversation_attachment_download_service = downloads
    app.include_router(build_conversation_attachment_router())
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        authorization=AuthorizationService(SimpleNamespace(permits=lambda *_: False)),
        routes=tuple(app.router.routes),
    )
    return TestClient(app), uploads, downloads


def authenticate(client, *, owner=True):
    client.cookies.set("session", "owner" if owner else "other")
    client.cookies.set("csrf", "csrf-token")
    return {"Origin": "https://agent.example.test", "X-CSRF-Token": "csrf-token"}


def test_member_routes_require_session_origin_and_csrf() -> None:
    client, uploads, _ = api_client()
    path = "/api/v1/attachments/uploads"
    body = {
        "conversation_id": str(CONVERSATION_ID),
        "original_name": "report.pdf",
        "declared_mime": "application/pdf",
        "declared_size": 7,
    }
    assert client.post(path, json=body).status_code == 401
    client.cookies.set("session", "owner")
    assert client.post(path, json=body).status_code == 403
    assert (
        client.post(
            path, json=body, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    response = client.post(path, json=body, headers=authenticate(client))
    assert response.status_code == 201
    assert uploads.calls[0][1] == OWNER_ID
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_validation_errors_do_not_echo_candidate_controlled_content() -> None:
    client, _, _ = api_client()
    response = client.post(
        "/api/v1/attachments/uploads",
        json={
            "conversation_id": str(CONVERSATION_ID),
            "original_name": "candidate-private-name.pdf",
            "declared_mime": "application/pdf",
            "declared_size": "candidate-private-content",
        },
        headers=authenticate(client),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "attachment request invalid"}
    assert "candidate-private" not in response.text


def test_upload_content_rejects_missing_or_mismatched_content_length() -> None:
    client, uploads, _ = api_client()
    headers = authenticate(client)
    path = f"/api/v1/attachments/uploads/{UPLOAD_ID}/content"
    assert (
        client.request(
            "PUT",
            path,
            content=b"payload",
            headers=headers | {"Transfer-Encoding": "chunked"},
        ).status_code
        == 411
    )
    assert (
        client.put(
            path, content=b"payload", headers=headers | {"Content-Length": "8"}
        ).status_code
        == 409
    )
    assert uploads.calls == []
    response = client.put(
        path, content=b"payload", headers=headers | {"Content-Length": "7"}
    )
    assert response.status_code == 200
    assert uploads.calls[-1][0:3] == ("write", OWNER_ID, UPLOAD_ID)


def test_lifecycle_routes_are_owner_scoped() -> None:
    client, uploads, downloads = api_client()
    headers = authenticate(client)
    assert (
        client.post(
            f"/api/v1/attachments/uploads/{UPLOAD_ID}/complete", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/attachments/uploads/{UPLOAD_ID}", headers=headers
        ).status_code
        == 204
    )
    assert client.get(f"/api/v1/attachments/{ATTACHMENT_ID}").status_code == 200
    assert (
        client.get(f"/api/v1/conversations/{CONVERSATION_ID}/attachments").status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/attachments/{ATTACHMENT_ID}", headers=headers
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/artifacts/download",
            headers=headers,
        ).status_code
        == 200
    )
    assert all(call[1] == OWNER_ID for call in uploads.calls + downloads.calls)


def test_content_route_ignores_storage_coordinates_and_sets_safe_headers() -> None:
    client, _, downloads = api_client()
    authenticate(client)
    response = client.get(
        "/api/v1/attachments/content/opaque-ticket?object_key=secret&locator=version:v1",
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 200
    assert downloads.calls[-1] == ("content", OWNER_ID, "opaque-ticket", "bytes=0-3")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


class AssetRepository:
    def __init__(self, assets):
        self.assets = {asset.attachment_id: asset for asset in assets}
        self.deleted = set()
        self.lock = threading.Lock()

    def downloadable(self, owner_id, attachment_id, purpose):
        asset = self.assets.get(attachment_id)
        if (
            asset is None
            or asset.owner_id != owner_id
            or asset.state != "ready"
            or attachment_id in self.deleted
        ):
            raise DownloadNotFound()
        return asset

    def list_current_artifacts(self, owner_id, conversation_id):
        return tuple(
            asset
            for asset in self.assets.values()
            if asset.owner_id == owner_id
            and asset.conversation_id == conversation_id
            and asset.state == "ready"
            and asset.artifact_key is not None
            and asset.attachment_id not in self.deleted
        )

    def request_erasure(self, owner_id, attachment_id):
        with self.lock:
            asset = self.assets.get(attachment_id)
            if asset is None or asset.owner_id != owner_id:
                raise DownloadNotFound()
            self.deleted.add(attachment_id)


class VerifiedStore:
    def __init__(self, values):
        self.values, self.opens = values, []

    def stage_verified(self, asset, directory):
        self.opens.append((asset.object_ref, asset.immutable_locator))
        value = self.values[asset.object_ref]
        if (
            len(value) != asset.size_bytes
            or hashlib.sha256(value).digest() != asset.sha256
        ):
            raise DownloadNotFound()
        target = Path(directory) / uuid4().hex
        target.write_bytes(value)
        return target


def download_asset(
    *,
    attachment_id=ATTACHMENT_ID,
    owner_id=OWNER_ID,
    conversation_id=CONVERSATION_ID,
    name="报告.pdf",
    mime="application/pdf",
    state="ready",
    object_ref="private-object",
    immutable_locator="version:immutable-v1",
    artifact_key=None,
    version_no=None,
):
    return DownloadAsset(
        attachment_id=attachment_id,
        owner_id=owner_id,
        conversation_id=conversation_id,
        display_name=name,
        media_type=mime,
        size_bytes=7,
        sha256=hashlib.sha256(b"payload").digest(),
        state=state,
        object_ref=object_ref,
        immutable_locator=immutable_locator,
        artifact_key=artifact_key,
        version_no=version_no,
    )


def service_for(*assets, ticket_seconds=300):
    repository = AssetRepository(assets)
    store = VerifiedStore({asset.object_ref: b"payload" for asset in assets})
    service = ConversationAttachmentDownloadService(
        repository,
        store,
        ticket_secret=b"t" * 32,
        ticket_seconds=ticket_seconds,
        clock=lambda: NOW,
    )
    return service, repository, store


def test_ticket_is_owner_scoped_ready_only_opaque_bounded_and_single_use() -> None:
    ready = download_asset()
    service, repository, _ = service_for(ready, ticket_seconds=999)
    with pytest.raises(DownloadNotFound):
        service.issue_ticket(OTHER_ID, ATTACHMENT_ID, "download")
    repository.assets[ATTACHMENT_ID] = replace(ready, state="scanning")
    with pytest.raises(DownloadNotFound):
        service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    repository.assets[ATTACHMENT_ID] = ready
    ticket = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    assert ticket.expires_at == NOW + timedelta(seconds=300)
    assert all(
        value not in ticket.ticket
        for value in (
            str(OWNER_ID),
            str(ATTACHMENT_ID),
            "private-object",
            "immutable-v1",
        )
    )
    assert (
        b"".join(service.open_content(OWNER_ID, ticket.ticket, None).stream)
        == b"payload"
    )
    with pytest.raises(DownloadNotFound):
        service.open_content(OWNER_ID, ticket.ticket, None)


def test_ticket_concurrent_replay_allows_exactly_one_consumer() -> None:
    service, _, _ = service_for(download_asset())
    ticket = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")

    def consume(_index):
        try:
            return b"".join(service.open_content(OWNER_ID, ticket.ticket, None).stream)
        except DownloadNotFound:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(8)))
    assert results.count(b"payload") == 1
    assert results.count(None) == 7


def test_ticket_expiry_owner_change_and_delete_fail_as_not_found() -> None:
    asset = download_asset()
    service, repository, _ = service_for(asset)
    wrong_owner = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    with pytest.raises(DownloadNotFound):
        service.open_content(OTHER_ID, wrong_owner.ticket, None)
    deleted = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    service.delete_attachment(OWNER_ID, ATTACHMENT_ID)
    with pytest.raises(DownloadNotFound):
        service.open_content(OWNER_ID, deleted.ticket, None)
    repository.deleted.clear()
    expiring, _, _ = service_for(asset, ticket_seconds=1)
    expired = expiring.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    expiring._clock = lambda: NOW + timedelta(seconds=2)
    with pytest.raises(DownloadNotFound):
        expiring.open_content(OWNER_ID, expired.ticket, None)


def test_download_consumes_locator_rechecks_digest_and_supports_range() -> None:
    service, _, store = service_for(download_asset())
    ticket = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    opened = service.open_content(OWNER_ID, ticket.ticket, "bytes=1-3")
    assert (
        opened.status_code,
        opened.headers["Content-Range"],
        b"".join(opened.stream),
    ) == (206, "bytes 1-3/7", b"ayl")
    assert opened.headers["Content-Length"] == "3"
    assert store.opens == [("private-object", "version:immutable-v1")]
    invalid = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, "download")
    with pytest.raises(DownloadRangeError) as captured:
        service.open_content(OWNER_ID, invalid.ticket, "bytes=9-10")
    assert captured.value.content_range == "bytes */7"


@pytest.mark.parametrize(
    "name,mime,purpose,expected",
    [
        ("evil\r\nX-Evil: yes.svg", "image/svg+xml", "preview", "attachment"),
        ("page.html", "text/html", "preview", "attachment"),
        (
            "resume.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "preview",
            "attachment",
        ),
        ("portrait.png", "image/png", "preview", "inline"),
        ("报告.pdf", "application/pdf", "download", "attachment"),
    ],
)
def test_disposition_is_rfc5987_clean_and_unsafe_types_attach(
    name, mime, purpose, expected
) -> None:
    service, _, _ = service_for(download_asset(name=name, mime=mime))
    ticket = service.issue_ticket(OWNER_ID, ATTACHMENT_ID, purpose)
    opened = service.open_content(OWNER_ID, ticket.ticket, None)
    disposition = opened.headers["Content-Disposition"]
    assert disposition.startswith(expected + ";")
    assert "filename*=UTF-8''" in disposition
    assert (
        "\r" not in disposition
        and "\n" not in disposition
        and "X-Evil" not in disposition
    )
    assert opened.headers["X-Content-Type-Options"] == "nosniff"


def test_archive_is_current_ready_owner_only_deterministic_and_zip_slip_safe(
    tmp_path,
) -> None:
    first = download_asset(
        attachment_id=uuid4(),
        name="../report.pdf",
        object_ref="first",
        artifact_key="analysis",
        version_no=2,
    )
    second = download_asset(
        attachment_id=uuid4(),
        name="report.pdf",
        object_ref="second",
        artifact_key="summary",
        version_no=1,
    )
    not_ready = replace(
        download_asset(
            attachment_id=uuid4(),
            object_ref="third",
            artifact_key="draft",
            version_no=1,
        ),
        state="scanning",
    )
    foreign = replace(
        download_asset(
            attachment_id=uuid4(),
            object_ref="foreign",
            artifact_key="foreign",
            version_no=1,
        ),
        owner_id=OTHER_ID,
    )
    service, _, _ = service_for(first, second, not_ready, foreign)
    service._temporary_root = tmp_path
    opened = service.archive_conversation(OWNER_ID, CONVERSATION_ID)
    archive_bytes = b"".join(opened.stream)
    assert list(tmp_path.iterdir()) == []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.namelist() == ["report.pdf", "report (2).pdf"]
        assert all(
            ".." not in name and not name.startswith("/") for name in archive.namelist()
        )
        assert [archive.read(name) for name in archive.namelist()] == [
            b"payload",
            b"payload",
        ]


def test_archive_enforces_file_cap_before_storage_reads() -> None:
    assets = tuple(
        download_asset(
            attachment_id=uuid4(),
            object_ref=f"object-{index}",
            artifact_key=f"artifact-{index:02}",
            version_no=1,
        )
        for index in range(21)
    )
    service, _, store = service_for(*assets)
    with pytest.raises(DownloadNotFound):
        service.archive_conversation(OWNER_ID, CONVERSATION_ID)
    assert store.opens == []


def test_archive_deduplication_cannot_collide_with_numbered_source_name() -> None:
    assets = (
        download_asset(attachment_id=uuid4(), name="report.pdf"),
        download_asset(attachment_id=uuid4(), name="report.pdf"),
        download_asset(attachment_id=uuid4(), name="report (2).pdf"),
    )

    names = ConversationAttachmentDownloadService._unique_archive_names(assets)

    assert names == ("report.pdf", "report (2).pdf", "report (2) (2).pdf")


class StorageClient:
    def __init__(self, value=b"payload"):
        self.value = value
        self.calls = []

    def get_object(self, **request):
        self.calls.append(request)
        return {"Body": io.BytesIO(self.value), "ContentLength": len(self.value)}


@pytest.mark.parametrize(
    ("locator", "coordinate"),
    [
        ("version:fixed-v1", ("VersionId", "fixed-v1")),
        ('etag:"fixed"', ("IfMatch", '"fixed"')),
    ],
)
def test_s3_store_consumes_persisted_locator_without_head(
    locator, coordinate, tmp_path
) -> None:
    client = StorageClient()
    store = S3ImmutableAttachmentStore(client, "private-bucket")
    asset = download_asset(immutable_locator=locator)

    staged = store.stage_verified(asset, tmp_path)

    assert staged.read_bytes() == b"payload"
    assert client.calls == [
        {
            "Bucket": "private-bucket",
            "Key": "private-object",
            coordinate[0]: coordinate[1],
        }
    ]


def test_s3_store_fails_closed_on_overwrite_or_missing_primary_locator(
    tmp_path,
) -> None:
    overwritten = StorageClient(b"PAYLOAD")
    store = S3ImmutableAttachmentStore(overwritten, "private-bucket")
    with pytest.raises(DownloadNotFound):
        store.stage_verified(download_asset(), tmp_path)
    assert list(tmp_path.iterdir()) == []

    missing = StorageClient()
    with pytest.raises(DownloadNotFound):
        S3ImmutableAttachmentStore(missing, "private-bucket").stage_verified(
            download_asset(immutable_locator=None), tmp_path
        )
    assert missing.calls == []


def test_archive_cleans_temporary_volume_when_client_disconnects(tmp_path) -> None:
    asset = download_asset(artifact_key="report", version_no=1)
    service, _, _ = service_for(asset)
    service._temporary_root = tmp_path
    opened = service.archive_conversation(OWNER_ID, CONVERSATION_ID)

    next(opened.stream)
    opened.stream.close()

    assert list(tmp_path.iterdir()) == []


@pytest.mark.postgres
def test_access_repository_rechecks_owner_ready_locator_and_erasure_atomically(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    codec = ContentCodec(
        IdentityKeyring(
            active_version=7,
            purpose="platform-content-encryption",
            _keys={7: b"7" * 32},
        )
    )
    owner_id, other_id, conversation_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Download Owner','active'),(%s,'Download Other','active')",
            (owner_id, other_id),
        )
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','Downloads','active')",
            (conversation_id, owner_id, uuid4()),
        )
    uploads = ConversationAttachmentRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    created = uploads.create_upload(
        owner_id, conversation_id, "private-report.pdf", "application/pdf", 7
    )
    attempt = uploads.claim_write(owner_id, created.upload_id)
    uploads.complete_upload(
        owner_id,
        created.upload_id,
        attempt.attempt_id,
        7,
        hashlib.sha256(b"payload").digest(),
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.attachments set state='ready',ready_at=now(),"
            "detected_mime='application/pdf',immutable_locator='version:fixed-v1' "
            "where attachment_id=%s",
            (created.attachment_id,),
        )
        connection.execute(
            "update platform_attachments.uploads set state='ready',"
            "detected_mime='application/pdf',immutable_locator='version:fixed-v1' "
            "where attachment_id=%s",
            (created.attachment_id,),
        )

    access = ConversationAttachmentAccessRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    selected = access.downloadable(owner_id, created.attachment_id, "download")
    assert selected.object_ref == attempt.object_ref
    assert selected.immutable_locator == "version:fixed-v1"
    with pytest.raises(DownloadNotFound):
        access.downloadable(owner_id, created.attachment_id, "preview")
    with pytest.raises(DownloadNotFound):
        access.downloadable(other_id, created.attachment_id, "download")

    access.request_erasure(owner_id, created.attachment_id)
    access.request_erasure(owner_id, created.attachment_id)
    with pytest.raises(DownloadNotFound):
        access.downloadable(owner_id, created.attachment_id, "download")
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_attachments.erasure_jobs "
            "where attachment_id=%s",
            (created.attachment_id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_cancel_upload_abandons_claimed_write_before_requesting_erasure(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    codec = ContentCodec(
        IdentityKeyring(
            active_version=7,
            purpose="platform-content-encryption",
            _keys={7: b"7" * 32},
        )
    )
    owner_id, conversation_id = uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Cancel Owner','active')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','Cancel','active')",
            (conversation_id, owner_id, uuid4()),
        )
    uploads = ConversationAttachmentRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )
    created = uploads.create_upload(
        owner_id, conversation_id, "partial.pdf", "application/pdf", 7
    )
    attempt = uploads.claim_write(owner_id, created.upload_id)
    access = ConversationAttachmentAccessRepository(
        environment["urls"]["platform_control_app"], content_codec=codec
    )

    access.cancel_upload(owner_id, created.upload_id)

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select state from platform_attachments.upload_write_attempts "
            "where attempt_id=%s",
            (attempt.attempt_id,),
        ).fetchone() == ("abandoned",)
        assert connection.execute(
            "select count(*) from platform_attachments.erasure_jobs "
            "where attachment_id=%s",
            (created.attachment_id,),
        ).fetchone() == (1,)
