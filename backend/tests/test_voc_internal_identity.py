from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.control_plane.identity import StaffIdentity
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, DirectoryFreshness, Role
from app.voc_extension.directory import SubmitterOption
from app.voc_extension.internal_identity import (
    PlatformVocBotSubjectResolver,
    VocBotSubject,
    VocServiceAuthorizer,
)
from app.voc_extension.internal_routes import build_voc_internal_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ID = UUID("22222222-2222-4222-8222-222222222222")


class Auth:
    cookie_name = "__Host-platform_session"
    csrf_cookie_name = "__Host-platform_csrf"
    route_prefix = "/"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None

    def authenticate(self, cookie: str):
        if cookie != "manager-session":
            return None
        return (
            AuthContext(USER_ID, Role.MANAGEMENT_VIEWER, uuid4(), False),
            "session-csrf",
        )

    def account_snapshot(self, context: AuthContext) -> dict[str, object]:
        assert context.internal_user_id == USER_ID
        return {"display_name": "艾琳"}

    def verify_csrf(self, value: str, digest: str) -> bool:
        return value == digest


class Directory:
    def names_for(self, ids):
        return {value: "艾琳" for value in ids if value == USER_ID}

    def list_submitters(self):
        return (
            SubmitterOption(USER_ID, "艾琳"),
            SubmitterOption(OTHER_ID, "博文"),
        )


class BotSubjects:
    async def resolve(self, staff_id: str):
        if staff_id == "staff-1":
            return VocBotSubject(USER_ID, True)
        if staff_id == "inactive-staff":
            return VocBotSubject(OTHER_ID, False)
        if staff_id == "unavailable":
            raise RuntimeError("provider-user-sensitive")
        return None


@pytest.mark.asyncio
async def test_platform_bot_subject_resolver_projects_inactive_identity() -> None:
    calls = []

    class Resolver:
        async def resolve_staff_member(self, staff_id, freshness):
            calls.append((staff_id, freshness))
            return StaffIdentity(USER_ID, False)

    resolver = PlatformVocBotSubjectResolver(
        identity_resolver=Resolver(),
        directory_freshness=lambda: DirectoryFreshness.FRESH,
    )

    assert await resolver.resolve("dingtalk-userid") == VocBotSubject(USER_ID, False)
    assert calls == [("dingtalk-userid", DirectoryFreshness.FRESH)]


def test_platform_bot_subject_resolver_preserves_inactive_route_contract(
    voc_bearer,
):
    calls = []

    class StaffResolver:
        async def resolve_staff_member(self, staff_id, freshness):
            calls.append((staff_id, freshness))
            return StaffIdentity(OTHER_ID, False)

    app = FastAPI()
    app.include_router(
        build_voc_internal_router(
            auth=Auth(),
            directory=Directory(),
            bearer=voc_bearer.encode("utf-8"),
            bot_subject_resolver=PlatformVocBotSubjectResolver(
                identity_resolver=StaffResolver(),
                directory_freshness=lambda: DirectoryFreshness.WARNING,
            ),
        )
    )

    with TestClient(app, client=("172.29.0.3", 50000)) as service:
        response = service.post(
            "/api/v1/internal/voc/bot-subject",
            headers={"Authorization": f"Bearer {voc_bearer}"},
            json={"staff_id": "inactive-dingtalk-userid"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "internal_user_id": str(OTHER_ID),
        "active": False,
        "capabilities": ["voc.read_self", "voc.submit"],
    }
    assert calls == [("inactive-dingtalk-userid", DirectoryFreshness.WARNING)]


@pytest.fixture
def voc_bearer() -> str:
    return "v" * 32


@pytest.fixture
def manager_cookie() -> str:
    return "manager-session"


@pytest.fixture
def client(voc_bearer: str):
    app = FastAPI()
    app.include_router(
        build_voc_internal_router(
            auth=Auth(),
            directory=Directory(),
            bot_subject_resolver=BotSubjects(),
            bearer=voc_bearer.encode("utf-8"),
        )
    )

    @app.middleware("http")
    async def session_state(request, call_next):
        if request.cookies.get("__Host-platform_session") == "manager-session":
            request.state.auth_context = AuthContext(
                USER_ID, Role.MANAGEMENT_VIEWER, uuid4(), False
            )
            request.state.csrf_token = "session-csrf"
        return await call_next(request)
    with TestClient(app, client=("172.29.0.3", 50000)) as result:
        yield result


def test_browser_subject_projects_platform_permissions(client, manager_cookie, voc_bearer):
    response = client.get(
        "/api/v1/internal/voc/browser-subject",
        cookies={"__Host-platform_session": manager_cookie},
        headers={"Authorization": f"Bearer {voc_bearer}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "internal_user_id": "11111111-1111-4111-8111-111111111111",
        "display_name": "艾琳",
        "read_only": False,
        "capabilities": ["voc.read_all", "voc.read_self", "voc.submit"],
        "csrf_token": "session-csrf",
    }


def test_voc_internal_routes_hide_from_public_and_reject_wrong_bearer(client):
    assert client.get("/api/v1/internal/voc/browser-subject").status_code == 404
    response = client.get(
        "/api/v1/internal/voc/browser-subject",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 404


def test_identity_middleware_hides_private_route_before_session_authentication(
    voc_bearer,
):
    app = FastAPI()
    authorizer = VocServiceAuthorizer(voc_bearer.encode("utf-8"))
    app.include_router(
        build_voc_internal_router(
            auth=Auth(), directory=Directory(), bearer=authorizer
        )
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=Auth(),
        public_assets=frozenset(),
        routes=tuple(app.router.routes),
        voc_service_authorizer=authorizer,
    )

    with TestClient(app, client=("172.29.0.3", 50000)) as protected:
        assert protected.get("/api/v1/internal/voc/browser-subject").status_code == 404
        response = protected.get(
            "/api/v1/internal/voc/browser-subject",
            headers={
                "Authorization": f"Bearer {voc_bearer}",
                "Cookie": "__Host-platform_session=manager-session; "
                "__Host-platform_csrf=session-csrf",
            },
        )

    assert response.status_code == 200
    assert response.json()["csrf_token"] == "session-csrf"


def test_bot_subject_is_strict_and_uses_directory_identity(client, voc_bearer):
    response = client.post(
        "/api/v1/internal/voc/bot-subject",
        headers={"Authorization": f"Bearer {voc_bearer}"},
        json={"staff_id": "staff-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "internal_user_id": str(USER_ID),
        "active": True,
        "capabilities": ["voc.read_self", "voc.submit"],
    }
    assert client.post(
        "/api/v1/internal/voc/bot-subject",
        headers={"Authorization": f"Bearer {voc_bearer}"},
        json={"staff_id": "staff-1", "role": "owner"},
    ).status_code == 422
    assert client.post(
        "/api/v1/internal/voc/bot-subject",
        headers={"Authorization": f"Bearer {voc_bearer}"},
        json={"staff_id": "inactive-staff"},
    ).json() == {
        "internal_user_id": str(OTHER_ID),
        "active": False,
        "capabilities": ["voc.read_self", "voc.submit"],
    }
    unavailable = client.post(
        "/api/v1/internal/voc/bot-subject",
        headers={"Authorization": f"Bearer {voc_bearer}"},
        json={"staff_id": "unavailable"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "directory unavailable"}
    assert "provider-user-sensitive" not in unavailable.text


def test_submitter_directory_exposes_only_canonical_public_projection(
    client, voc_bearer
):
    headers = {"Authorization": f"Bearer {voc_bearer}"}
    resolved = client.post(
        "/api/v1/internal/voc/submitter-directory/resolve",
        headers=headers,
        json={"internal_user_ids": [str(USER_ID), str(OTHER_ID)]},
    )

    assert resolved.status_code == 200
    assert resolved.json() == [
        {"internal_user_id": str(USER_ID), "display_name": "艾琳"}
    ]
    assert client.get(
        "/api/v1/internal/voc/submitter-directory/options", headers=headers
    ).json() == [
        {"internal_user_id": str(USER_ID), "display_name": "艾琳"},
        {"internal_user_id": str(OTHER_ID), "display_name": "博文"},
    ]
