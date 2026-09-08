from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.hr.panorama_repository import (
    PanoramaConflict,
    PanoramaNotFound,
    PanoramaUnavailable,
)
from app.hr.panorama_routes import build_panorama_router


class FakeService:
    def __init__(self) -> None:
        self.bundle_id = uuid4()
        self.error = None
        self.empty = False
        self.calls = []
        self.report_value = {"publication": {"publication_id": str(self.bundle_id), "bundle_id": str(self.bundle_id), "manifest_sha256": "a" * 64}, "insight": {"summary": "已发布招聘情报"}, "sources": [], "snapshots": [], "evidence": [], "analysis_usage": []}

    def _return(self, value):
        if self.error: raise self.error
        return value

    def current_report(self): self.calls.append(("current",)); return self._return(None if self.empty else self.report_value)
    def companies(self): self.calls.append(("companies",)); return self._return(None)
    def company(self, company_key, *, bundle_id=None): self.calls.append(("company", company_key, bundle_id)); return self._return({})
    def company_jobs(self, company_key, **kwargs): self.calls.append(("company_jobs", company_key, kwargs)); return self._return({})
    def list_reports(self, *, limit=100): self.calls.append(("list", limit)); return self._return((self.report_value,))
    def report(self, bundle_id): self.calls.append(("report", bundle_id)); return self._return(self.report_value)
    def document(self, bundle_id, format):
        self.calls.append(("document", bundle_id, format))
        mime = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "md": "text/markdown; charset=utf-8"}[format]
        return {"name": f"report.{format}", "mime": mime, "sha256": "b" * 64, "body": f"prebuilt-{format}".encode()}
    def evidence_file(self, bundle_id, sha256): self.calls.append(("evidence", bundle_id, sha256)); return {"name": sha256, "mime": "application/json", "sha256": sha256, "body": b"source"}


def _client(*, entitled=True):
    service = FakeService(); app = FastAPI()
    async def require_hr_access(_request: Request, *, writable=False):
        assert writable is False
        if not entitled: raise HTTPException(403, "HR Agent use denied")
        return uuid4()
    app.include_router(build_panorama_router(service, require_hr_access))
    return TestClient(app), service


def test_panorama_router_has_only_get_routes() -> None:
    router = build_panorama_router(FakeService(), lambda _request, **_: uuid4())
    methods = {method for route in router.routes for method in (getattr(route, "methods", set()) or set()) if route.path.startswith("/api/hr/panorama")}
    assert methods == {"GET"}


def test_current_returns_204_when_no_bundle_is_published() -> None:
    client, service = _client(); service.empty = True
    response = client.get("/api/hr/panorama/current")
    assert response.status_code == 204 and response.content == b""


@pytest.mark.parametrize("format", ["pdf", "xlsx", "md"])
def test_export_returns_verified_prebuilt_document(format) -> None:
    client, service = _client()
    response = client.get(f"/api/hr/panorama/reports/{service.bundle_id}/export?format={format}")
    assert response.status_code == 200
    assert response.content == f"prebuilt-{format}".encode()
    assert response.headers["x-content-sha256"] == "b" * 64
    assert service.calls == [("document", service.bundle_id, format)]


def test_export_rejects_runtime_filter_parameters() -> None:
    client, service = _client()
    response = client.get(f"/api/hr/panorama/reports/{service.bundle_id}/export?format=pdf&location=深圳")
    assert response.status_code == 422
    assert service.calls == []


def test_evidence_returns_verified_hash_header() -> None:
    client, service = _client(); sha = "c" * 64
    response = client.get(f"/api/hr/panorama/reports/{service.bundle_id}/evidence/{sha}")
    assert response.status_code == 200 and response.content == b"source"
    assert response.headers["x-content-sha256"] == sha


@pytest.mark.parametrize(("error", "status"), [(PanoramaNotFound(), 404), (PanoramaConflict(), 409), (PanoramaUnavailable(), 503), (TypeError(), 422)])
def test_errors_are_sanitized(error, status) -> None:
    client, service = _client(); service.error = error
    response = client.get("/api/hr/panorama/current")
    assert response.status_code == status and response.headers["cache-control"] == "private, no-store"


def test_unentitled_user_is_blocked_before_service() -> None:
    client, service = _client(entitled=False)
    assert client.get("/api/hr/panorama/current").status_code == 403
    assert service.calls == []
