from __future__ import annotations

import json
from datetime import UTC, datetime
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


class Directory:
    def __init__(self) -> None:
        self.names = {USER_ID: "苍渊"}
        self.error: Exception | None = None

    def names_for(self, ids):
        if self.error is not None:
            raise self.error
        return {item: self.names[item] for item in ids if item in self.names}

    def list_submitters(self):
        if self.error is not None:
            raise self.error
        from app.voc_extension.directory import SubmitterOption

        return (SubmitterOption(USER_ID, "苍渊"),)


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


def workspace_app(
    upstream: Upstream | None,
    *,
    stale: bool = False,
    role: Role = Role.MEMBER,
    directory: Directory | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.voc_extension_client = upstream
    app.state.voc_submitter_directory = directory or Directory()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = AuthContext(
            USER_ID, role, uuid4(), stale
        )
        return await call_next(request)

    app.include_router(build_voc_extension_router())
    return app


def admin_summary(**overrides) -> dict[str, object]:
    summary: dict[str, object] = {
        "voc_no": "VOC-20260826-001",
        "submitter_internal_user_id": str(USER_ID),
        "legacy_submitter_name": None,
        "source": "platform",
        "latest_content": "设备连续运行三小时后明显发热",
        "revision": 2,
        "analysis_status": "claimed",
        "created_at": "2026-08-26T09:30:00Z",
        "updated_at": "2026-08-26T09:35:00Z",
    }
    summary.update(overrides)
    return summary


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


@pytest.mark.asyncio
async def test_member_cannot_call_management_routes_or_reach_upstream() -> None:
    upstream = Upstream()
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream)), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/extensions/voc/admin/vocs")

    assert response.status_code == 403
    assert upstream.calls == []


@pytest.mark.asyncio
async def test_manager_list_forwards_filters_with_read_all_and_enriches_names() -> None:
    upstream = Upstream()
    upstream.response = VocUpstreamResponse(
        200,
        json.dumps(
            {"items": [admin_summary()], "next_cursor": "next.page"}
        ).encode(),
    )
    params = {
        "query": "发热",
        "submitter_internal_user_id": str(USER_ID),
        "legacy_submitter_name": "历史提交人",
        "created_from": "2026-08-01T00:00:00Z",
        "created_to": "2026-09-01T00:00:00Z",
        "cursor": "opaque.page",
        "limit": "50",
    }
    async with AsyncClient(
        transport=ASGITransport(
            app=workspace_app(upstream, role=Role.MANAGEMENT_VIEWER)
        ),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/extensions/voc/admin/vocs", params=params
        )

    assert response.status_code == 200
    assert response.json()["items"][0] == {
        key: value
        for key, value in admin_summary().items()
        if key != "legacy_submitter_name"
    } | {"submitter_name": "苍渊"}
    assert response.json()["next_cursor"] == "next.page"
    call = upstream.calls[-1]
    assert call["path"] == "/api/platform/v1/admin/vocs"
    assert call["capabilities"] == frozenset({"voc.read_all"})
    assert call["query"] == {
        "query": "发热",
        "submitter_internal_user_id": USER_ID,
        "legacy_submitter_name": "历史提交人",
        "created_from": datetime(2026, 8, 1, tzinfo=UTC),
        "created_to": datetime(2026, 9, 1, tzinfo=UTC),
        "cursor": "opaque.page",
        "limit": 50,
    }
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_manager_list_uses_legacy_and_unknown_submitter_fallbacks() -> None:
    unknown_id = UUID("44444444-4444-4444-8444-444444444444")
    upstream = Upstream()
    upstream.response = VocUpstreamResponse(
        200,
        json.dumps(
            {
                "items": [
                    admin_summary(
                        voc_no="VOC-20260826-002",
                        submitter_internal_user_id=None,
                        legacy_submitter_name="历史同事",
                        source="dingtalk",
                    ),
                    admin_summary(
                        voc_no="VOC-20260826-003",
                        submitter_internal_user_id=str(unknown_id),
                    ),
                    admin_summary(
                        voc_no="VOC-20260826-004",
                        submitter_internal_user_id=None,
                        legacy_submitter_name=None,
                        source="dingtalk",
                    ),
                ],
                "next_cursor": None,
            }
        ).encode(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream, role=Role.PLATFORM_ADMIN)),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/extensions/voc/admin/vocs")

    assert response.status_code == 200
    assert [item["submitter_name"] for item in response.json()["items"]] == [
        "历史同事",
        "未知用户 · 44444444",
        "历史提交人",
    ]
    assert all("legacy_submitter_name" not in item for item in response.json()["items"])


@pytest.mark.asyncio
async def test_manager_detail_enriches_entries_and_submitter_options() -> None:
    upstream = Upstream()
    detail = admin_summary() | {
        "entries": [
            {
                "revision": 1,
                "entry_type": "original",
                "content": "设备发热",
                "created_at": "2026-08-26T09:30:00Z",
            }
        ]
    }
    upstream.response = VocUpstreamResponse(200, json.dumps(detail).encode())
    app = workspace_app(upstream, role=Role.PLATFORM_OWNER)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/extensions/voc/admin/vocs/VOC-20260826-001"
        )
        submitters = await client.get(
            "/api/v1/extensions/voc/admin/submitters"
        )

    assert response.status_code == 200
    assert response.json()["submitter_name"] == "苍渊"
    assert response.json()["entries"] == detail["entries"]
    assert upstream.calls[-1]["capabilities"] == frozenset({"voc.read_all"})
    assert submitters.json() == {
        "items": [
            {"internal_user_id": str(USER_ID), "display_name": "苍渊"}
        ]
    }


@pytest.mark.asyncio
async def test_management_rejects_malformed_upstream_and_safe_directory_failure() -> None:
    from app.voc_extension.directory import VocDirectoryUnavailable

    upstream = Upstream()
    upstream.response = VocUpstreamResponse(
        200,
        json.dumps(
            {"items": [admin_summary(unexpected="secret")], "next_cursor": None}
        ).encode(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app(upstream, role=Role.PLATFORM_ADMIN)),
        base_url="http://test",
    ) as client:
        malformed = await client.get("/api/v1/extensions/voc/admin/vocs")

    directory = Directory()
    directory.error = VocDirectoryUnavailable("private DSN")
    upstream.response = VocUpstreamResponse(
        200,
        json.dumps({"items": [admin_summary()], "next_cursor": None}).encode(),
    )
    async with AsyncClient(
        transport=ASGITransport(
            app=workspace_app(
                upstream, role=Role.PLATFORM_ADMIN, directory=directory
            )
        ),
        base_url="http://test",
    ) as client:
        unavailable = await client.get("/api/v1/extensions/voc/admin/vocs")

    assert malformed.status_code == 502
    assert malformed.json() == {"detail": "voc_protocol_error"}
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "voc_directory_unavailable"}
