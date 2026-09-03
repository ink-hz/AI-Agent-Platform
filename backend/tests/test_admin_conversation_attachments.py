from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.control_plane.models import AuthContext, Role
from app.review.routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

ACTOR_ID = UUID("00000000-0000-0000-0000-000000000099")
CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000011")
ATTACHMENT_ID = UUID("00000000-0000-0000-0000-000000000012")
FEEDBACK_ID = UUID("00000000-0000-0000-0000-000000000013")
NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.triage = None

    def review_conversation_attachments(self, conversation_id):
        assert conversation_id == CONVERSATION_ID
        attachment = SimpleNamespace(
            attachment_id=ATTACHMENT_ID,
            conversation_id=CONVERSATION_ID,
            source="user",
            display_name="访谈记录.pdf",
            detected_mime="application/pdf",
            size_bytes=2048,
            state="quarantined",
            created_at=NOW,
            retained_until=NOW,
            processing_coverage={"pages": 3},
            availability_reason="quarantined",
        )
        return (
            SimpleNamespace(
                attachment=attachment,
                artifact_key=None,
                version_no=None,
                current=False,
            ),
        )

    def triage_feedback(self, actor_id, feedback_id, status):
        self.triage = (actor_id, feedback_id, status)
        return SimpleNamespace(
            feedback_id=feedback_id,
            conversation_id=CONVERSATION_ID,
            message_id=UUID("00000000-0000-0000-0000-000000000014"),
            turn_id=UUID("00000000-0000-0000-0000-000000000015"),
            mission_id=None,
            rating="unhelpful",
            reason="file_format",
            comment="表格格式不对",
            triage_status=status,
            triaged_by_internal_user_id=actor_id,
            triaged_at=NOW,
            created_at=NOW,
        )


class DownloadService:
    def __init__(self) -> None:
        self.issued = None

    def issue_review_ticket(self, actor_id, attachment_id, purpose):
        self.issued = (actor_id, attachment_id, purpose)
        return SimpleNamespace(
            ticket="opaque",
            expires_at=NOW,
            content_path="/api/v1/attachments/content/opaque",
        )


def application(role: Role = Role.PLATFORM_OWNER):
    app = FastAPI()
    repository = Repository()
    downloads = DownloadService()
    app.state.conversation_repository = repository
    app.state.conversation_attachment_download_service = downloads

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = AuthContext(
            internal_user_id=ACTOR_ID,
            role=role,
            session_id=UUID("00000000-0000-0000-0000-000000000098"),
            hard_stale_read_only=False,
        )
        return await call_next(request)

    app.include_router(router)
    return app, repository, downloads


def test_owner_can_project_attachment_metadata_but_not_unavailable_content():
    app, _, _ = application()

    response = TestClient(app).get(
        f"/api/review/conversations/{CONVERSATION_ID}/attachments"
    )

    assert response.status_code == 200
    assert response.json() == [{
        "attachment_id": str(ATTACHMENT_ID),
        "conversation_id": str(CONVERSATION_ID),
        "source": "user",
        "display_name": "访谈记录.pdf",
        "detected_mime": "application/pdf",
        "size_bytes": 2048,
        "state": "quarantined",
        "created_at": NOW.isoformat(),
        "retained_until": NOW.isoformat(),
        "processing_coverage": {"pages": 3},
        "availability_reason": "quarantined",
        "artifact_key": None,
        "version_no": None,
        "current": False,
    }]


def test_review_ticket_is_fresh_and_bound_to_the_owner_actor():
    app, _, downloads = application()

    response = TestClient(app).post(
        f"/api/review/attachments/{ATTACHMENT_ID}/ticket",
        json={"purpose": "download"},
    )

    assert response.status_code == 200
    assert response.json()["content_path"] == "/api/v1/attachments/content/opaque"
    assert downloads.issued == (ACTOR_ID, ATTACHMENT_ID, "download")


def test_member_cannot_list_or_issue_review_attachment_tickets():
    app, _, _ = application(Role.MEMBER)
    client = TestClient(app)

    assert client.get(
        f"/api/review/conversations/{CONVERSATION_ID}/attachments"
    ).status_code == 403
    assert client.post(
        f"/api/review/attachments/{ATTACHMENT_ID}/ticket",
        json={"purpose": "preview"},
    ).status_code == 403


def test_owner_triage_records_actor_and_only_accepts_terminal_triage_states():
    app, repository, _ = application()
    client = TestClient(app)

    response = client.patch(
        f"/api/review/conversation-feedback/{FEEDBACK_ID}",
        json={"triage_status": "triaged"},
    )

    assert response.status_code == 200
    assert response.json()["triaged_by_internal_user_id"] == str(ACTOR_ID)
    assert repository.triage == (ACTOR_ID, FEEDBACK_ID, "triaged")
    assert client.patch(
        f"/api/review/conversation-feedback/{FEEDBACK_ID}",
        json={"triage_status": "pending_triage"},
    ).status_code == 422
