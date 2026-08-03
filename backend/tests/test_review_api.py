from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.review.repository import ConcurrentUpdate
from app.review.routes import router


ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeService:
    async def create_issue(self, payload, *, actor):
        return {"issue": {"id": str(ISSUE_ID), **payload.model_dump()}}

    async def update_issue(self, issue_id, payload, *, actor):
        raise ConcurrentUpdate(
            {"id": str(issue_id), "row_version": 2, "owner": "fae:bob"}
        )

    async def overview(self):
        return {"negative_turns": 50}


@pytest.fixture
def app():
    application = FastAPI()
    application.state.review_service = FakeService()
    application.include_router(router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_writes_require_accountable_actor(client):
    response = client.post(
        "/api/review/issues",
        json={"agent_id": "ai-fae-agent", "title": "issue", "priority": "P1"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("actor", ["", "web-reviewer", "anonymous"])
def test_unaccountable_actor_is_rejected(client, actor):
    response = client.post(
        "/api/review/issues",
        json={"agent_id": "ai-fae-agent", "title": "issue", "priority": "P1"},
        headers={"X-Review-Actor": actor},
    )

    assert response.status_code == 422


def test_stale_patch_returns_latest_row(client):
    response = client.patch(
        f"/api/review/issues/{ISSUE_ID}",
        json={"row_version": 1, "owner": "fae:alice", "reason": "assign"},
        headers={"X-Review-Actor": "fae:alice"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current"]["row_version"] == 2


def test_status_cannot_be_written(client):
    response = client.patch(
        f"/api/review/issues/{ISSUE_ID}",
        json={"row_version": 1, "status": "closed", "reason": "force"},
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 422


def test_machine_verification_result_cannot_be_supplied_by_client(client):
    response = client.post(
        f"/api/review/issues/{ISSUE_ID}/evidence",
        json={
            "evidence_type": "merge",
            "reference": "merge commit",
            "commit_sha": "a" * 40,
            "verification_status": "verified",
        },
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 422


def test_api_has_no_close_operation(app):
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert all(not path.endswith("/close") for path in paths)
    assert all("force-close" not in path for path in paths)


def test_read_endpoint_does_not_require_actor(client):
    assert client.get("/api/review/overview").json() == {"negative_turns": 50}
