from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.control_plane.models import AuthContext, Role
from app.review.repository import ConcurrentUpdate
from app.review.routes import router
from app.review.service import ReviewService
from fastapi import FastAPI
from fastapi.testclient import TestClient

ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")
LINK_ID = UUID("00000000-0000-0000-0000-000000000002")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000003")


class FakeService:
    def __init__(self):
        self.move = None
        self.link = None
        self.read_filters = []

    async def create_issue(self, payload, *, actor):
        return {"issue": {"id": str(ISSUE_ID), **payload.model_dump()}}

    async def update_issue(self, issue_id, payload, *, actor):
        raise ConcurrentUpdate(
            {"id": str(issue_id), "row_version": 2, "owner": "fae:bob"}
        )

    async def overview(self, *, agent_id=None):
        self.read_filters.append(("overview", agent_id))
        return {"negative_turns": 50}

    async def inbox(self, *, agent_id=None, limit, offset):
        self.read_filters.append(("inbox", agent_id))
        return []

    async def list_issues(self, *, agent_id=None, limit, offset):
        self.read_filters.append(("issues", agent_id))
        return []

    async def turn_summaries(self, *, turn_keys):
        self.read_filters.append(("turn_summaries", turn_keys))
        return [{"turn_key": turn_keys[0], "status": "pending_triage"}]

    async def move_link(self, issue_id, link_id, payload, *, actor):
        self.move = (issue_id, link_id, payload.target_issue_id, actor)
        return {"issue": {"id": str(payload.target_issue_id)}}

    async def link_turn(self, issue_id, payload, *, actor):
        self.link = (issue_id, payload.model_dump(), actor)
        return {"issue": {"id": str(issue_id)}}


@pytest.fixture
def app():
    application = FastAPI()
    application.state.review_service = FakeService()

    @application.middleware("http")
    async def conversation_review_identity(request, call_next):
        if request.url.path.startswith("/api/review/conversation") or request.url.path.startswith("/api/review/attachments"):
            role = Role(request.headers.get("X-Test-Role", Role.PLATFORM_OWNER.value))
            request.state.auth_context = AuthContext(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000099"),
                role=role,
                session_id=UUID("00000000-0000-0000-0000-000000000098"),
                hard_stale_read_only=False,
            )
        return await call_next(request)

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


def test_read_endpoints_forward_agent_filter_and_batch_turn_keys(client, app):
    assert client.get(
        "/api/review/overview?agent_id=ai-fae-agent"
    ).status_code == 200
    assert client.get(
        "/api/review/inbox?agent_id=ai-fae-agent"
    ).status_code == 200
    assert client.get(
        "/api/review/issues?agent_id=ai-fae-agent"
    ).status_code == 200
    response = client.get(
        "/api/review/turn-summaries",
        params=[("turn_key", "fae:one"), ("turn_key", "fae:two")],
    )

    assert response.status_code == 200
    assert app.state.review_service.read_filters[-4:] == [
        ("overview", "ai-fae-agent"),
        ("inbox", "ai-fae-agent"),
        ("issues", "ai-fae-agent"),
        ("turn_summaries", ["fae:one", "fae:two"]),
    ]


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


def test_generic_link_keeps_agent_id_and_accepts_turn_without_feedback(client, app):
    response = client.post(
        f"/api/review/issues/{ISSUE_ID}/links",
        json={
            "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-ordinary",
            "source_feedback_keys": [],
            "link_role": "primary",
            "reason": "create from inspected answer",
        },
        headers={"X-Review-Actor": "codex"},
    )

    assert response.status_code == 201
    assert app.state.review_service.link == (
        ISSUE_ID,
        {
            "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-ordinary",
            "source_feedback_keys": [],
            "link_role": "primary",
            "reason": "create from inspected answer",
        },
        "codex",
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/review/issues",
            {
                "agent_id": "ai-fae-agent",
                "origin_turn_key": "admin:turn",
                "title": "foreign origin",
            },
        ),
        (
            f"/api/review/issues/{ISSUE_ID}/links",
            {
                "agent_id": "ai-fae-agent",
                "source_turn_key": "admin:turn",
                "source_feedback_keys": [],
            },
        ),
        (
            f"/api/review/issues/{TARGET_ID}/links",
            {
                "agent_id": "ai-fae-agent",
                "source_turn_key": "fae:turn",
                "source_feedback_keys": ["fae:feedback"],
            },
        ),
    ],
)
def test_generic_api_rejects_cross_agent_create_and_link_before_writer(path, payload):
    class ReadRepository:
        def get_issue_detail(self, issue_id):
            agent_id = "ai-fae-agent" if issue_id == ISSUE_ID else "admin-agent"
            return {"issue": {"id": issue_id, "agent_id": agent_id}}

        def feedback_keys_for_turn(self, agent_id, turn_key):
            if (agent_id, turn_key) == ("ai-fae-agent", "fae:turn"):
                return {"turn_key": turn_key, "feedback_keys": ["fae:feedback"]}
            return None

    class Writer:
        def __getattr__(self, name):
            if name in {"create_issue", "link_turn"}:
                raise AssertionError("cross-Agent mutation must not reach writer")
            raise AttributeError(name)

    application = FastAPI()
    application.state.review_service = ReviewService(
        ReadRepository(), write_repository=Writer()
    )
    application.include_router(router)

    with TestClient(application) as scoped_client:
        response = scoped_client.post(
            path,
            json=payload,
            headers={"X-Review-Actor": "codex"},
        )

    assert response.status_code == 409
    assert "agent" in response.json()["detail"] or "turn" in response.json()["detail"]


def test_read_only_service_keeps_get_available_and_rejects_writes():
    class ReadRepository:
        def overview(self, *, agent_id=None):
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


def test_conversation_feedback_projection_exposes_context_and_triage_state(
    client,
    app,
):
    app.state.conversation_repository = SimpleNamespace(
        list_feedback_for_review=lambda **filters: (
            (
                SimpleNamespace(
                    feedback=SimpleNamespace(
                        feedback_id=UUID("00000000-0000-0000-0000-000000000010"),
                        conversation_id=UUID("00000000-0000-0000-0000-000000000011"),
                        message_id=UUID("00000000-0000-0000-0000-000000000012"),
                        turn_id=UUID("00000000-0000-0000-0000-000000000013"),
                        mission_id=UUID("00000000-0000-0000-0000-000000000014"),
                        rating="unhelpful",
                        reason="incomplete",
                        comment="请补充证据来源。",
                        triage_status="pending_triage",
                        triaged_by_internal_user_id=None,
                        triaged_at=None,
                        created_at=datetime.fromisoformat("2026-08-23T10:00:00+00:00"),
                    ),
                    agent_id="hr-bot",
                    conversation_title="候选人搜索",
                    question="搜索研发岗位",
                    answer="以下是已核验岗位。",
                    citations=(),
                ),
            ),
            1,
        )
    )

    response = client.get("/api/review/conversation-feedback?triage_status=pending_triage&limit=20&offset=0")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["rating"] == "unhelpful"
    assert response.json()["items"][0]["reason"] == "incomplete"
    assert response.json()["items"][0]["comment"] == "请补充证据来源。"
    assert response.json()["items"][0]["question"] == "搜索研发岗位"
    assert response.json()["items"][0]["answer"] == "以下是已核验岗位。"
    assert response.json()["items"][0]["agent_id"] == "hr-bot"
    assert response.json()["items"][0]["triage_status"] == "pending_triage"


def test_conversation_feedback_is_platform_owner_only(client, app):
    app.state.conversation_repository = SimpleNamespace(
        list_feedback_for_review=lambda **filters: ((), 0)
    )

    response = client.get(
        "/api/review/conversation-feedback",
        headers={"X-Test-Role": "member"},
    )

    assert response.status_code == 403
