from uuid import UUID

from app.control_plane.models import AuthContext, Role
from app.fae_reports import routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Service:
    def list(self, status=None):
        return [{"report_id": "fae-topic-production-through-20260831", "status": "ready"}]

    def latest(self):
        return {"report_id": "fae-topic-production-through-20260831", "status": "ready"}

    def detail(self, report_id, version=None):
        if report_id != "fae-topic-production-through-20260831":
            return None
        return {"report_id": report_id, "report_version": version or 1, "status": "ready"}


def app(role):
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
    value.include_router(routes.router)
    return value


def test_owner_can_list_read_latest_and_versioned_report():
    client = TestClient(app(Role.PLATFORM_OWNER))
    assert client.get("/api/admin/fae/reports").status_code == 200
    assert client.get("/api/admin/fae/reports/latest").json()["status"] == "ready"
    detail = client.get(
        "/api/admin/fae/reports/fae-topic-production-through-20260831?version=1"
    )
    assert detail.json()["report_version"] == 1


def test_member_is_denied_and_unknown_report_is_404():
    assert TestClient(app(Role.MEMBER)).get("/api/admin/fae/reports").status_code == 403
    assert TestClient(app(Role.PLATFORM_OWNER)).get(
        "/api/admin/fae/reports/fae-topic-missing"
    ).status_code == 404
