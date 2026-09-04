from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.hr.resource_models import PositionArtifactItem, PositionMaterialItem
from app.hr.resource_routes import build_hr_resource_router
from app.hr.resource_service import HrPositionResourceService, ResourceNotFound

OWNER = UUID("00000000-0000-4000-8000-000000000001")
POSITION = UUID("00000000-0000-4000-8000-000000000002")
OTHER = UUID("00000000-0000-4000-8000-000000000003")
MATERIAL = UUID("00000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 9, 4, tzinfo=UTC)


class Resources:
    def position_exists(self, owner_id, position_id):
        return (owner_id, position_id) == (OWNER, POSITION)

    def _allowed(self, owner_id, position_id):
        if (owner_id, position_id) != (OWNER, POSITION):
            raise ResourceNotFound("position resource not found")

    def materials_for_position(self, owner_id, position_id):
        self._allowed(owner_id, position_id)
        return (PositionMaterialItem(
            attachment_id=MATERIAL, filename="职位材料.pdf", media_type="application/pdf",
            state="ready", size_bytes=8, created_at=NOW, source_conversation_id=None,
            source_turn_id=None, preview_available=True, download_available=True,
        ),)

    def artifacts_for_position(self, owner_id, position_id):
        self._allowed(owner_id, position_id)
        return (PositionArtifactItem(
            artifact_id=uuid4(), attachment_id=uuid4(), artifact_version=1,
            filename="产出.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            state="ready", size_bytes=8, created_at=NOW, source_conversation_id=uuid4(),
            source_turn_id=uuid4(), preview_available=False, download_available=True,
        ),)


class Tickets:
    def issue_ticket(self, owner_id, attachment_id, purpose):
        return {"content_path": "/opaque", "expires_at": "2026-09-04T00:05:00+00:00"}


def require_hr_access(_request: Request):
    return OWNER


def client():
    app = FastAPI()
    app.include_router(build_hr_resource_router(
        HrPositionResourceService(Resources(), Tickets()), require_hr_access,
    ))
    return TestClient(app)


def test_resources_return_exact_public_metadata_without_storage_locator():
    response = client().get(f"/api/hr/positions/{POSITION}/resources")

    assert response.status_code == 200
    material = response.json()["materials"][0]
    assert material["attachment_id"] == str(MATERIAL)
    assert material["filename"] == "职位材料.pdf"
    assert "immutable_locator" not in material
    assert response.headers["cache-control"] == "private, no-store"


def test_cross_position_resource_is_not_visible():
    response = client().get(f"/api/hr/positions/{OTHER}/resources/{MATERIAL}")

    assert response.status_code == 404


def test_missing_position_resource_collection_is_concealed_as_not_found():
    response = client().get(f"/api/hr/positions/{OTHER}/resources")

    assert response.status_code == 404
