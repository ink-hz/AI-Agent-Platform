from uuid import UUID

from app.control_plane.models import AuthContext, Role
from app.fae_workbench import routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Service:
    def list_summaries(self, status=None):
        return [{"report_id": "fae-topic-production-through-20260831", "status": "ready"}]

    def latest(self):
        return {"report_id": "fae-topic-production-through-20260831", "status": "ready"}

    def detail(self, report_id, version=None):
        if report_id != "fae-topic-production-through-20260831":
            return None
        return {"report_id": report_id, "report_version": version or 1, "status": "ready"}


class FaeAccess:
    def __init__(self, granted: bool = False):
        self.granted = granted

    def allows(self, context):
        return context.role is Role.PLATFORM_OWNER or self.granted


def app(role, *, granted=False):
    value = FastAPI()

    @value.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = AuthContext(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000001"),
            role=role,
            session_id=UUID("00000000-0000-0000-0000-000000000002"),
            hard_stale_read_only=False,
        )
        return await call_next(request)

    value.state.fae_report_service = Service()
    value.state.fae_access = FaeAccess(granted)
    value.include_router(routes.router, prefix="/api/fae")
    value.include_router(routes.router, prefix="/api/admin/fae", include_in_schema=False)
    return value


def test_owner_can_list_read_latest_and_versioned_report():
    client = TestClient(app(Role.PLATFORM_OWNER))
    index = client.get("/api/admin/fae/reports")
    assert index.status_code == 200
    assert "metrics" not in index.json()[0]
    assert client.get("/api/admin/fae/reports/latest").json()["status"] == "ready"
    detail = client.get(
        "/api/admin/fae/reports/fae-topic-production-through-20260831?version=1"
    )
    assert detail.json()["report_version"] == 1
    assert client.get("/api/fae/reports/latest").json()["status"] == "ready"


def test_report_context_permits_granted_member_and_denies_ungranted_admin():
    assert TestClient(app(Role.MEMBER, granted=True)).get(
        "/api/fae/reports"
    ).status_code == 200
    assert TestClient(app(Role.PLATFORM_ADMIN)).get(
        "/api/admin/fae/reports"
    ).status_code == 403


def test_ungranted_member_is_denied_and_unknown_report_is_404():
    assert TestClient(app(Role.MEMBER)).get("/api/admin/fae/reports").status_code == 403
    assert TestClient(app(Role.MANAGEMENT_VIEWER)).get(
        "/api/admin/fae/reports"
    ).status_code == 403
    assert TestClient(app(Role.PLATFORM_OWNER)).get(
        "/api/admin/fae/reports/fae-topic-missing"
    ).status_code == 404
