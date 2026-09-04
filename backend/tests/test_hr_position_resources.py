from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.hr.resource_models import PositionArtifactItem, PositionMaterialItem
from app.hr.resource_service import (
    HrPositionResourceService,
    PsycopgPositionResourceRepository,
    ResourceNotFound,
)


OWNER = UUID("00000000-0000-4000-8000-000000000001")
POSITION = UUID("00000000-0000-4000-8000-000000000002")
OTHER = UUID("00000000-0000-4000-8000-000000000003")
MATERIAL = UUID("00000000-0000-4000-8000-000000000004")
ARTIFACT = UUID("00000000-0000-4000-8000-000000000005")
TURN = UUID("00000000-0000-4000-8000-000000000006")
NOW = datetime(2026, 9, 4, tzinfo=UTC)


class Resources:
    def materials_for_position(self, owner_id, position_id):
        if (owner_id, position_id) != (OWNER, POSITION):
            raise ResourceNotFound("position resource not found")
        return (PositionMaterialItem(
            attachment_id=MATERIAL, filename="岗位说明.pdf", media_type="application/pdf",
            state="ready", size_bytes=12, created_at=NOW, source_conversation_id=None,
            source_turn_id=None, preview_available=True, download_available=True,
        ),)

    def artifacts_for_position(self, owner_id, position_id):
        if (owner_id, position_id) != (OWNER, POSITION):
            raise ResourceNotFound("position resource not found")
        return (PositionArtifactItem(
            artifact_id=ARTIFACT, attachment_id=uuid4(), artifact_version=2,
            filename="面试方案.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            state="ready", size_bytes=20, created_at=NOW, source_conversation_id=uuid4(),
            source_turn_id=TURN, preview_available=False, download_available=True,
        ),)


class Tickets:
    def __init__(self):
        self.calls = []

    def issue_ticket(self, owner_id, attachment_id, purpose):
        self.calls.append((owner_id, attachment_id, purpose))
        return {"content_path": "/api/conversations/attachments/content/opaque", "expires_at": "2026-09-04T00:05:00+00:00"}


@pytest.fixture
def tickets():
    return Tickets()


@pytest.fixture
def service(tickets):
    return HrPositionResourceService(Resources(), tickets)


def test_position_resources_return_exact_metadata_not_only_ids(service):
    resources = service.for_position(OWNER, POSITION)

    assert resources.materials[0].attachment_id == MATERIAL
    assert resources.materials[0].filename == "岗位说明.pdf"
    assert resources.artifacts[0].source_turn_id == TURN
    assert resources.artifacts[0].artifact_version == 2
    assert not hasattr(resources.artifacts[0], "immutable_locator")


def test_download_delegates_to_existing_ticket_service(service, tickets):
    ticket = service.ticket(OWNER, POSITION, MATERIAL, "download")

    assert ticket.content_path.endswith("/opaque")
    assert tickets.calls == [(OWNER, MATERIAL, "download")]


def test_cross_position_resource_is_not_visible(service):
    with pytest.raises(ResourceNotFound):
        service.for_position(OWNER, OTHER)


class Connection:
    def __init__(self):
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))
        return self

    def fetchall(self):
        return [{"attachment_id": MATERIAL, "source_conversation_id": None, "source_turn_id": None, "created_at": NOW}]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Attachments:
    def attachment(self, owner_id, attachment_id):
        assert (owner_id, attachment_id) == (OWNER, MATERIAL)
        return type("Attachment", (), {
            "display_name": "岗位说明.pdf", "detected_mime": "application/pdf",
            "size_bytes": 12, "state": "ready", "created_at": NOW,
        })()


def test_psycopg_projection_queries_one_exact_owner_position_binding():
    connection = Connection()
    repository = PsycopgPositionResourceRepository(lambda: connection, Attachments())

    materials = repository.materials_for_position(OWNER, POSITION)

    assert materials[0].attachment_id == MATERIAL
    query, params = connection.queries[0]
    assert "position_id=%s" in query and "owner_internal_user_id=%s" in query
    assert params == (OWNER, POSITION)
