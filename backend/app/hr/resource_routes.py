"""Private HR endpoints for resources explicitly linked to one position."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from .resource_models import PositionArtifactItem, PositionMaterialItem, PositionResourceTicket
from .resource_service import HrPositionResourceService, ResourceNotFound, ResourceUnavailable
from .routes import HrPositionRoute


class TicketBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    purpose: Literal["preview", "download"]


def _material(value: PositionMaterialItem) -> dict[str, object]:
    return {
        "attachment_id": str(value.attachment_id), "filename": value.filename,
        "media_type": value.media_type, "state": value.state, "size_bytes": value.size_bytes,
        "created_at": value.created_at.isoformat(),
        "source_conversation_id": str(value.source_conversation_id) if value.source_conversation_id else None,
        "source_turn_id": str(value.source_turn_id) if value.source_turn_id else None,
        "preview_available": value.preview_available, "download_available": value.download_available,
    }


def _artifact(value: PositionArtifactItem) -> dict[str, object]:
    return {
        "artifact_id": str(value.artifact_id), "attachment_id": str(value.attachment_id),
        "artifact_version": value.artifact_version, **_material(PositionMaterialItem(
            value.attachment_id, value.filename, value.media_type, value.state, value.size_bytes,
            value.created_at, value.source_conversation_id, value.source_turn_id,
            value.preview_available, value.download_available,
        )),
    }


def _ticket(value: PositionResourceTicket) -> dict[str, object]:
    return {
        "content_path": value.content_path,
        "expires_at": value.expires_at.isoformat() if hasattr(value.expires_at, "isoformat") else value.expires_at,
    }


def build_hr_resource_router(service, require_hr_access) -> APIRouter:
    if not isinstance(service, HrPositionResourceService):
        raise ValueError("HR position resource service required")
    if not callable(require_hr_access):
        raise ValueError("HR access dependency required")
    router = APIRouter(route_class=HrPositionRoute)

    @router.get("/api/hr/positions/{position_id}/resources")
    def resources(
        position_id: Annotated[UUID, Path()],
        owner_id: UUID = Depends(require_hr_access),
    ):
        try:
            found = service.for_position(owner_id, position_id)
        except ResourceNotFound:
            raise HTTPException(404, "position resource not found") from None
        except (ResourceUnavailable, ValueError):
            raise HTTPException(503, "position resources unavailable") from None
        return {"materials": [_material(value) for value in found.materials], "artifacts": [_artifact(value) for value in found.artifacts]}

    @router.get("/api/hr/positions/{position_id}/resources/{attachment_id}")
    def resource(
        position_id: Annotated[UUID, Path()], attachment_id: Annotated[UUID, Path()],
        owner_id: UUID = Depends(require_hr_access),
    ):
        try:
            found = service.for_position(owner_id, position_id)
        except ResourceNotFound:
            raise HTTPException(404, "position resource not found") from None
        except (ResourceUnavailable, ValueError):
            raise HTTPException(503, "position resources unavailable") from None
        for value in (*found.materials, *found.artifacts):
            if value.attachment_id == attachment_id:
                return _material(value) if isinstance(value, PositionMaterialItem) else _artifact(value)
        raise HTTPException(404, "position resource not found")

    @router.post("/api/hr/positions/{position_id}/resources/{attachment_id}/ticket")
    def ticket(
        position_id: Annotated[UUID, Path()], attachment_id: Annotated[UUID, Path()], body: TicketBody,
        owner_id: UUID = Depends(require_hr_access),
    ):
        try:
            return _ticket(service.ticket(owner_id, position_id, attachment_id, body.purpose))
        except ResourceNotFound:
            raise HTTPException(404, "position resource not found") from None
        except (ResourceUnavailable, ValueError):
            raise HTTPException(503, "position resource unavailable") from None

    return router
