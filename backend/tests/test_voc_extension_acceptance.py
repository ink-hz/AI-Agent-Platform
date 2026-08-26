from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.registry.repository import YamlRepository
from app.voc_extension.client import VocExtensionClient
from app.voc_extension.identity import PlatformVocTokenSigner
from app.voc_extension.routes import build_voc_extension_router

MEMBER_ID = UUID("11111111-1111-4111-8111-111111111111")


class Grants:
    def permits(self, _actor, _agent_id):
        return False


class Auth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None
    hard_stale_audit = staticmethod(lambda *_args: None)

    def authenticate(self, token):
        if token not in {"member", "stale"}:
            return None
        return (
            AuthContext(MEMBER_ID, Role.MEMBER, uuid4(), token == "stale"),
            "csrf",
        )

    def verify_csrf(self, value, digest):
        return value == digest


def _payload(token: str) -> dict[str, object]:
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


@pytest.mark.asyncio
async def test_platform_voc_extension_end_to_end_actor_and_mutation_boundary() -> None:
    downstream: list[httpx.Request] = []

    def voc_service(request: httpx.Request) -> httpx.Response:
        downstream.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "voc-workspace"})
        return httpx.Response(200, json={"ok": True})

    voc_client = VocExtensionClient(
        "http://127.0.0.1:18130",
        PlatformVocTokenSigner(b"v" * 32),
        transport=httpx.MockTransport(voc_service),
    )
    app = FastAPI()
    app.state.voc_extension_client = voc_client
    app.include_router(build_voc_extension_router())
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        authorization=AuthorizationService(Grants()),
        routes=tuple(app.router.routes),
    )
    mutation_headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": "csrf",
    }
    member_headers = {
        **mutation_headers,
        "Cookie": "session=member; csrf=csrf",
    }
    request_id = str(uuid4())
    submit_request_id = str(uuid4())
    draft_id = str(uuid4())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://agent.example.test"
        ) as browser:
            assert (await browser.get("/api/v1/extensions/voc/health")).status_code == 200
            assert (await browser.get("/api/v1/extensions/voc/vocs")).status_code == 401
            created = await browser.post(
                "/api/v1/extensions/voc/drafts",
                headers=member_headers,
                json={"request_id": request_id, "source_text": "客户说设备发热"},
            )
            assert created.status_code == 200
            actor_claim = _payload(downstream[-1].headers["Authorization"].removeprefix("Bearer "))
            assert actor_claim["sub"] == str(MEMBER_ID)

            count = len(downstream)
            spoofed = await browser.post(
                "/api/v1/extensions/voc/drafts",
                headers=member_headers,
                json={"request_id": request_id, "source_text": "反馈", "internal_user_id": str(uuid4())},
            )
            assert spoofed.status_code == 422
            assert len(downstream) == count

            submit_body = {"request_id": submit_request_id, "expected_version": 1}
            for _attempt in range(2):
                submitted = await browser.post(
                    f"/api/v1/extensions/voc/drafts/{draft_id}/submit",
                    headers=member_headers,
                    json=submit_body,
                )
                assert submitted.status_code == 200
            assert [json.loads(call.content)["request_id"] for call in downstream[-2:]] == [submit_request_id, submit_request_id]

            count = len(downstream)
            stale = await browser.post(
                "/api/v1/extensions/voc/drafts",
                headers={
                    **mutation_headers,
                    "Cookie": "session=stale; csrf=csrf",
                },
                json={"request_id": str(uuid4()), "source_text": "反馈"},
            )
            assert stale.status_code == 503
            assert len(downstream) == count
    finally:
        await voc_client.aclose()


def test_live_registry_exposes_only_the_native_voc_workspace() -> None:
    voc = YamlRepository("../registry.yaml").get_agent("voc")

    assert voc is not None
    assert voc.name == "VOC 洞察助手"
    assert voc.entry_url == "/agents/voc/workspace"
    assert voc.api_base is None
    assert set(voc.tags) == {"platform-extension", "employee-self-service"}
    assert voc.health.url == "http://127.0.0.1:8080/api/v1/extensions/voc/health"


def test_voc_extension_runbook_preserves_the_private_service_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    runbook = (root / "docs/voc-extension-runbook.md").read_text(encoding="utf-8")

    assert "VOC Platform extension" in readme
    assert "PLATFORM_VOC_EXTENSION_ENABLED=0" in runbook
    assert runbook.index("PLATFORM_VOC_EXTENSION_ENABLED=0") < runbook.index("停止 VOC workspace")
    assert "172.30.0.8:18130" in runbook
    assert "没有宿主机端口映射" in runbook
    assert "不要把身份令牌" in runbook
