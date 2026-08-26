from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.models import AuthContext, Role
from app.voc_extension.client import (
    VocProtocolError,
    VocUpstreamResponse,
    VocUpstreamUnavailable,
)
from app.voc_extension.routes import build_voc_extension_router

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER_ID = UUID("33333333-3333-4333-8333-333333333333")


class Upstream:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = VocUpstreamResponse(200, b'{"items":[]}')
        self.error: Exception | None = None

    async def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def workspace_app(upstream: Upstream | None, *, stale: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.voc_extension_client = upstream

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = AuthContext(
            USER_ID, Role.MEMBER, uuid4(), stale
        )
        return await call_next(request)

    app.include_router(build_voc_extension_router())
    return app


@pytest.mark.asyncio
async def test_member_actor_is_server_derived_and_body_cannot_spoof() -> None:
    upstream = Upstream()
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/extensions/voc/drafts",
            json={
                "request_id": str(uuid4()),
                "source_text": "客户说设备发热",
                "internal_user_id": str(OTHER_USER_ID),
            },
        )

    assert response.status_code == 422
    assert upstream.calls == []


@pytest.mark.asyncio
async def test_all_routes_forward_only_fixed_paths_and_authenticated_actor() -> None:
    upstream = Upstream()
    draft_id = uuid4()
    request_id = uuid4()
    content = {
        "customer": None,
        "feedback": "设备发热",
        "product_or_scenario": None,
        "impact": None,
        "evidence_basis": "employee_relay",
        "gaps": ["客户名称未知"],
    }
    cases = [
        ("POST", "/api/v1/extensions/voc/drafts", {"request_id": str(request_id), "source_text": "客户说设备发热"}, "/api/platform/v1/drafts"),
        ("GET", "/api/v1/extensions/voc/drafts/active", None, "/api/platform/v1/drafts/active"),
        ("PATCH", f"/api/v1/extensions/voc/drafts/{draft_id}", {"request_id": str(request_id), "expected_version": 1, "content": content}, f"/api/platform/v1/drafts/{draft_id}"),
        ("POST", f"/api/v1/extensions/voc/drafts/{draft_id}/cancel", {"request_id": str(request_id), "expected_version": 1}, f"/api/platform/v1/drafts/{draft_id}/cancel"),
        ("POST", f"/api/v1/extensions/voc/drafts/{draft_id}/submit", {"request_id": str(request_id), "expected_version": 1}, f"/api/platform/v1/drafts/{draft_id}/submit"),
        ("GET", "/api/v1/extensions/voc/vocs?query=%E5%8F%91%E7%83%AD&limit=10", None, "/api/platform/v1/vocs"),
        ("GET", "/api/v1/extensions/voc/vocs/VOC-20260826-001", None, "/api/platform/v1/vocs/VOC-20260826-001"),
        ("POST", "/api/v1/extensions/voc/vocs/VOC-20260826-001/supplements", {"request_id": str(request_id), "content": "客户补充会自动关机"}, "/api/platform/v1/vocs/VOC-20260826-001/supplements"),
    ]
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)),
        base_url="http://test",
    ) as client:
        for method, path, body, _downstream in cases:
            response = await client.request(method, path, json=body)
            assert response.status_code == 200

    assert [call["path"] for call in upstream.calls] == [case[3] for case in cases]
    assert all(call["actor_id"] == USER_ID for call in upstream.calls)
    assert upstream.calls[5]["query"] == {"query": "发热", "limit": 10}


@pytest.mark.asyncio
async def test_disabled_unavailable_and_invalid_upstream_fail_safely() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(None)), base_url="http://test"
    ) as client:
        disabled = await client.get("/api/v1/extensions/voc/vocs")
    assert disabled.status_code == 503
    assert disabled.json() == {"detail": "voc_unavailable"}

    upstream = Upstream()
    upstream.error = VocUpstreamUnavailable("private detail")
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)), base_url="http://test"
    ) as client:
        unavailable = await client.get("/api/v1/extensions/voc/vocs")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "voc_unavailable"}

    upstream.error = None
    upstream.response = VocUpstreamResponse(200, b"not-json")
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)), base_url="http://test"
    ) as client:
        invalid = await client.get("/api/v1/extensions/voc/vocs")
    assert invalid.status_code == 502
    assert invalid.json() == {"detail": "voc_protocol_error"}
    assert "private detail" not in json.dumps(invalid.json())

    upstream.response = VocUpstreamResponse(200, b'{"value":NaN}')
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)), base_url="http://test"
    ) as client:
        nonstandard = await client.get("/api/v1/extensions/voc/vocs")
    assert nonstandard.status_code == 502
    assert nonstandard.json() == {"detail": "voc_protocol_error"}

    upstream.error = VocProtocolError("private protocol detail")
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)), base_url="http://test"
    ) as client:
        protocol = await client.get("/api/v1/extensions/voc/vocs")
    assert protocol.status_code == 502
    assert protocol.json() == {"detail": "voc_protocol_error"}
