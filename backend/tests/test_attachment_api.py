import io
import logging
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.attachments.logging import AttachmentTicketRedactionFilter
from app.attachments.models import OpenedAttachment, Ticket
from app.attachments.routes import router
from app.attachments.service import (
    AttachmentConflict,
    AttachmentNotFound,
    AttachmentRangeError,
)
from app.config import load_config
from app.main import create_app


ATTACHMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeService:
    def __init__(self):
        self.calls = []
        self.ticket = Ticket(
            ticket="opaque-value",
            expires_at=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
            content_path="/api/attachments/content/opaque-value",
        )
        self.opened = OpenedAttachment(
            stream=iter([b"payload"]),
            status_code=200,
            media_type="application/pdf",
            headers={"Cache-Control": "private, no-store"},
        )
        self.error = None

    def issue_ticket(self, attachment_id, purpose):
        self.calls.append(("issue", attachment_id, purpose))
        if self.error:
            raise self.error
        return self.ticket

    def open_content(self, ticket, byte_range, context):
        self.calls.append(("open", ticket, byte_range, context))
        if self.error:
            raise self.error
        return self.opened


def make_client(service, **kwargs):
    app = FastAPI()
    app.state.attachment_service = service
    app.include_router(router)
    return TestClient(app, **kwargs)


def assert_private_attachment_headers(response) -> None:
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_ticket_endpoint_has_strict_purpose_and_safe_response() -> None:
    service = FakeService()
    client = make_client(service)

    response = client.post(
        f"/api/attachments/{ATTACHMENT_ID}/ticket", json={"purpose": "preview"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "ticket": "opaque-value",
        "expires_at": "2026-08-06T12:00:00Z",
        "content_path": "/api/attachments/content/opaque-value",
    }
    assert_private_attachment_headers(response)
    invalid = client.post(
        f"/api/attachments/{ATTACHMENT_ID}/ticket", json={"purpose": "execute"}
    )
    assert invalid.status_code == 422
    assert_private_attachment_headers(invalid)


def test_content_endpoint_uses_only_path_ticket_and_rejects_storage_coordinates() -> None:
    service = FakeService()
    client = make_client(service)

    response = client.get(
        "/api/attachments/content/opaque?bucket=evil&key=private",
        headers={"Range": "bytes=0-3", "X-Request-ID": "req-1"},
    )

    assert response.status_code == 200
    call = service.calls[0]
    assert call[0:3] == ("open", "opaque", "bytes=0-3")
    assert call[3]["request_id"] == "req-1"
    assert "bucket" not in call[3] and "key" not in call[3]
    assert_private_attachment_headers(response)


def test_attachment_errors_map_to_safe_http_statuses() -> None:
    service = FakeService()
    client = make_client(service)
    for error, status in (
        (AttachmentNotFound("not found"), 404),
        (AttachmentConflict("use download"), 409),
        (AttachmentRangeError("invalid range"), 416),
    ):
        service.error = error
        response = client.get("/api/attachments/content/opaque")
        assert response.status_code == status
        assert "opaque" not in response.text
        assert_private_attachment_headers(response)


def test_attachment_domain_500_has_private_headers_and_safe_detail() -> None:
    service = FakeService()
    service.error = RuntimeError("private storage coordinates")
    client = make_client(service, raise_server_exceptions=False)

    response = client.get("/api/attachments/content/opaque")

    assert response.status_code == 500
    assert response.json() == {"detail": "attachment service unavailable"}
    assert_private_attachment_headers(response)


def test_uvicorn_access_filter_redacts_ticket_in_formatted_output() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(AttachmentTicketRedactionFilter())
    logger = logging.getLogger("test.attachment.access")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:123",
        "GET",
        "/api/attachments/content/very-secret?download=1",
        "1.1",
        200,
    )

    emitted = stream.getvalue()
    assert "very-secret" not in emitted
    assert "/api/attachments/content/[REDACTED]?download=1" in emitted
    assert 'GET' in emitted and '200' in emitted and "127.0.0.1:123" in emitted


def test_uvicorn_access_filter_handles_preformatted_fallback() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(AttachmentTicketRedactionFilter())
    logger = logging.getLogger("test.attachment.access.fallback")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("GET /api/attachments/content/fallback-secret HTTP/1.1")

    assert "fallback-secret" not in stream.getvalue()
    assert "/api/attachments/content/[REDACTED]" in stream.getvalue()


def _app_paths(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    return registry, contract


def test_disabled_mode_does_not_register_attachment_routes(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    registry, contract = _app_paths(tmp_path)
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
    )

    assert TestClient(app).get(
        "/api/attachments/content/opaque"
    ).status_code == 404


def test_injected_attachment_service_registers_routes_without_real_storage(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    registry, contract = _app_paths(tmp_path)
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        attachment_service=FakeService(),
    )

    assert TestClient(app).post(
        f"/api/attachments/{ATTACHMENT_ID}/ticket",
        json={"purpose": "download"},
    ).status_code == 200


def test_enabled_attachment_construction_failure_aborts_app_startup(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    config = replace(load_config(), attachment_enabled=True)
    monkeypatch.setattr("app.main.load_config", lambda: config)
    monkeypatch.setattr(
        "app.main.build_attachment_service",
        lambda _config: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
    )
    registry, contract = _app_paths(tmp_path)

    try:
        create_app(
            registry_path=str(registry),
            cluster_contract_path=str(contract),
            start_poller=False,
        )
    except RuntimeError as error:
        assert str(error) == "storage unavailable"
    else:
        raise AssertionError("enabled attachment startup did not fail closed")
