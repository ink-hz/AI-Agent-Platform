from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.attachments.conversation_models import AttachmentRecord
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
EXPIRED = UUID("00000000-0000-4000-8000-000000000007")
NOW = datetime(2026, 9, 4, tzinfo=UTC)


class Resources:
    def position_exists(self, owner_id, position_id):
        return (owner_id, position_id) == (OWNER, POSITION)

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
    def __init__(self, rows=None):
        self.queries = []
        self.rows = rows

    def execute(self, query, params):
        self.queries.append((query, params))
        return self

    def fetchall(self):
        return self.rows or [{"attachment_id": MATERIAL, "source_conversation_id": None, "source_turn_id": None, "created_at": NOW}]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Attachments:
    def attachment(self, owner_id, attachment_id):
        assert (owner_id, attachment_id) == (OWNER, MATERIAL)
        return AttachmentRecord(
            attachment_id=attachment_id, owner_id=owner_id, conversation_id=None,
            original_name="岗位说明.pdf", declared_mime="application/pdf",
            detected_mime=None, size_bytes=12, sha256=b"a" * 32, state="ready",
            created_at=NOW, retained_until=datetime(2027, 9, 4, tzinfo=UTC),
        )


def test_psycopg_projection_queries_one_exact_owner_position_binding():
    connection = Connection()
    repository = PsycopgPositionResourceRepository(lambda: connection, Attachments())

    materials = repository.materials_for_position(OWNER, POSITION)

    assert materials[0].filename == "岗位说明.pdf"
    query, params = connection.queries[0]
    assert "position_id=%s" in query and "owner_internal_user_id=%s" in query
    assert params == (OWNER, POSITION)


def test_repository_conceals_missing_position_before_returning_empty_resources():
    class MissingConnection(Connection):
        def fetchone(self):
            return None

    repository = PsycopgPositionResourceRepository(lambda: MissingConnection(), Attachments())

    assert repository.position_exists(OWNER, OTHER) is False


def test_artifact_projection_reads_all_versions_and_uses_version_creation_time():
    connection = Connection()
    repository = PsycopgPositionResourceRepository(lambda: connection, Attachments())

    try:
        repository.artifacts_for_position(OWNER, POSITION)
    except Exception:
        pass

    query, params = connection.queries[0]
    assert "artifact_versions version" in query
    assert "current_artifact_versions" not in query
    assert "version.created_at" in query
    assert "version.result_status" in query
    assert params == (OWNER, POSITION)


def test_resource_projection_uses_download_boundaries_and_degrades_one_unreadable_row():
    rows = [
        {
            "attachment_id": EXPIRED, "source_conversation_id": None,
            "source_turn_id": None, "created_at": NOW,
            "attachment_state": "ready", "detected_mime": "application/pdf",
            "declared_mime": "application/pdf", "attachment_size_bytes": 7,
            "attachment_created_at": NOW, "resource_state": "ready",
            "download_available": True, "preview_available": True,
        },
        {
            "attachment_id": MATERIAL, "source_conversation_id": None,
            "source_turn_id": None, "created_at": NOW,
            "attachment_state": "ready", "detected_mime": "application/pdf",
            "declared_mime": "application/pdf", "attachment_size_bytes": 12,
            "attachment_created_at": NOW, "resource_state": "ready",
            "download_available": True, "preview_available": True,
        },
    ]

    class PartiallyUnreadable(Attachments):
        def attachment(self, owner_id, attachment_id):
            if attachment_id == EXPIRED:
                raise RuntimeError("encrypted metadata unavailable")
            return super().attachment(owner_id, attachment_id)

    connection = Connection(rows)
    repository = PsycopgPositionResourceRepository(lambda: connection, PartiallyUnreadable())

    resources = repository.materials_for_position(OWNER, POSITION)

    assert len(resources) == 2
    assert resources[0].filename.startswith("不可用文件")
    assert resources[0].state == "unavailable"
    assert resources[0].download_available is False
    assert resources[0].preview_available is False
    assert resources[1].filename == "岗位说明.pdf"
    assert resources[1].preview_available is True
    query, _ = connection.queries[0]
    assert "retained_until>now()" in query
    assert "immutable_locator is not null" in query
    assert "erasure_jobs" in query
    assert "derivatives" in query
