from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.review.repository import ConcurrentUpdate
from app.review.routes import router
from app.review.service import ReviewService


ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
LINK_ID = UUID("00000000-0000-0000-0000-000000000002")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000003")


class FakeService:
    def __init__(self):
        self.move = None

    async def create_issue(self, payload, *, actor):
        return {"issue": {"id": str(ISSUE_ID), **payload.model_dump()}}

    async def update_issue(self, issue_id, payload, *, actor):
        raise ConcurrentUpdate(
            {"id": str(issue_id), "row_version": 2, "owner": "fae:bob"}
        )

    async def overview(self):
        return {"negative_turns": 50}

    async def move_link(self, issue_id, link_id, payload, *, actor):
        self.move = (issue_id, link_id, payload.target_issue_id, actor)
        return {"issue": {"id": str(payload.target_issue_id)}}


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


@pytest.mark.parametrize(
    "override",
    [
        {"environment": "dev"},
        {"environment": "production", "observed_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_deployment_evidence_time_and_environment_cannot_weaken_gate(
    client, override
):
    response = client.post(
        f"/api/review/issues/{ISSUE_ID}/evidence",
        json={
            "evidence_type": "deployment",
            "reference": "deployment artifact",
            "release_manifest_ref": "release.json",
            **override,
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


def test_link_can_be_moved_to_correct_canonical_issue(client, app):
    response = client.post(
        f"/api/review/issues/{ISSUE_ID}/links/{LINK_ID}/move",
        json={"target_issue_id": str(TARGET_ID), "reason": "correct grouping"},
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 200
    assert app.state.review_service.move == (
        ISSUE_ID,
        LINK_ID,
        TARGET_ID,
        "codex",
    )


def test_read_only_service_keeps_get_available_and_rejects_writes():
    class ReadRepository:
        def overview(self):
            return {"negative_turns": 7}

    application = FastAPI()
    application.state.review_service = ReviewService(
        ReadRepository(),
        write_repository=None,
    )
    application.include_router(router)

    with TestClient(application) as read_only_client:
        read_response = read_only_client.get("/api/review/overview")
        write_response = read_only_client.post(
            "/api/review/issues",
            json={
                "agent_id": "ai-fae-agent",
                "title": "issue",
                "priority": "P1",
            },
            headers={"X-Review-Actor": "codex"},
        )

    assert read_response.status_code == 200
    assert read_response.json() == {
        "negative_turns": 7,
        "write_available": False,
    }
    assert write_response.status_code == 503
    assert write_response.json()["detail"] == "feedback review unavailable"
