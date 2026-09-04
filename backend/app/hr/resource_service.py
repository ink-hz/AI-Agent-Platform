"""Read exact HR position resources without exposing attachment storage details."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from app.attachments.download_service import DownloadNotFound

from .resource_models import (
    PositionArtifactItem,
    PositionMaterialItem,
    PositionResources,
    PositionResourceTicket,
)


class ResourceNotFound(RuntimeError):
    """The caller does not own this exact position resource."""


class ResourceUnavailable(RuntimeError):
    """A resource projection or attachment ticket could not be read safely."""


class PositionResourceRepository(Protocol):
    def position_exists(self, owner_id: UUID, position_id: UUID) -> bool: ...

    def materials_for_position(self, owner_id: UUID, position_id: UUID) -> tuple[PositionMaterialItem, ...]: ...

    def artifacts_for_position(self, owner_id: UUID, position_id: UUID) -> tuple[PositionArtifactItem, ...]: ...


class AttachmentTicketService(Protocol):
    def issue_ticket(self, owner_id: UUID, attachment_id: UUID, purpose: Literal["preview", "download"]): ...


def _preview_available(media_type: str, state: str) -> bool:
    return state == "ready" and (media_type.startswith("image/") or media_type == "application/pdf")


class PsycopgPositionResourceRepository:
    """Projects only rows already bound to an owned HR position.

    Attachment names and metadata stay behind the existing attachment reader, which
    has the encryption and retention checks required for a public projection.
    """

    def __init__(self, connection_factory: Callable[[], object], attachments) -> None:
        if not callable(connection_factory) or not callable(getattr(attachments, "attachment", None)):
            raise ValueError("position resource repository invalid")
        self._connection = connection_factory
        self._attachments = attachments

    def _item(self, owner_id: UUID, row, *, artifact_id: UUID | None = None, artifact_version: int | None = None):
        try:
            attachment = self._attachments.attachment(owner_id, row["attachment_id"])
        except DownloadNotFound:  # one erased/unreadable historical row must not hide siblings
            attachment = None
        media_type = (
            getattr(attachment, "detected_mime", None)
            or getattr(attachment, "declared_mime", None)
            or row.get("detected_mime")
            or row.get("declared_mime")
            or "application/octet-stream"
        )
        attachment_state = getattr(attachment, "state", None) or row.get("attachment_state")
        state = "unavailable" if attachment is None else row.get("resource_state") or attachment_state
        filename = getattr(attachment, "original_name", None) or f"不可用文件 {str(row['attachment_id'])[:8]}"
        size_bytes = getattr(attachment, "size_bytes", None)
        if size_bytes is None:
            size_bytes = row.get("attachment_size_bytes", 0)
        created_at = row.get("created_at") or getattr(attachment, "created_at", None) or row.get("attachment_created_at")
        ready = state == "ready" and attachment_state == "ready"
        download_available = attachment is not None and (bool(row["download_available"]) if "download_available" in row else ready)
        preview_available = attachment is not None and (bool(row["preview_available"]) if "preview_available" in row else _preview_available(media_type, state) and ready)
        if artifact_id is None:
            return PositionMaterialItem(
                row["attachment_id"], filename, media_type, state, size_bytes, created_at,
                row.get("source_conversation_id"), row.get("source_turn_id"),
                preview_available and download_available, download_available,
            )
        return PositionArtifactItem(
            artifact_id, row["attachment_id"], artifact_version, filename, media_type, state,
            size_bytes, created_at, row.get("source_conversation_id"), row.get("source_turn_id"),
            preview_available and download_available, download_available,
        )

    def position_exists(self, owner_id: UUID, position_id: UUID) -> bool:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position resource identifiers required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select 1 from platform_hr.positions where "
                    "owner_internal_user_id=%s and position_id=%s",
                    (owner_id, position_id),
                ).fetchone()
            return row is not None
        except Exception as error:
            raise ResourceUnavailable("position resources unavailable") from error

    def materials_for_position(self, owner_id: UUID, position_id: UUID) -> tuple[PositionMaterialItem, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position resource identifiers required")
        query = (
            "select material.attachment_id, attachment.conversation_id as source_conversation_id, "
            "(select binding.turn_id from platform_attachments.bindings binding "
            "where binding.attachment_id=material.attachment_id and binding.turn_id is not null "
            "order by binding.created_at desc limit 1) as source_turn_id, material.created_at, "
            "attachment.state as attachment_state,attachment.detected_mime,attachment.declared_mime,"
            "attachment.size_bytes as attachment_size_bytes,attachment.created_at as attachment_created_at,"
            "case when exists(select 1 from platform_attachments.erasure_jobs erasure where "
            "erasure.attachment_id=attachment.attachment_id) then 'erasure_pending' "
            "when attachment.retained_until<=now() then 'expired' "
            "when attachment.state='ready' and attachment.immutable_locator is null then 'unavailable' "
            "else attachment.state end as resource_state,"
            "(attachment.state='ready' and attachment.retained_until>now() and "
            "attachment.immutable_locator is not null and not exists(select 1 from "
            "platform_attachments.erasure_jobs erasure where erasure.attachment_id=attachment.attachment_id)) "
            "as download_available,"
            "exists(select 1 from platform_attachments.derivatives derivative where "
            "derivative.attachment_id=attachment.attachment_id and derivative.state='ready' and "
            "derivative.retained_until>now() and "
            "derivative.kind in ('thumbnail','preview')) as preview_available "
            "from platform_hr.position_materials material "
            "join platform_attachments.attachments attachment using (attachment_id) "
            "where material.owner_internal_user_id=%s and material.position_id=%s and material.active "
            "order by material.updated_at desc"
        )
        try:
            with self._connection() as connection:
                rows = connection.execute(query, (owner_id, position_id)).fetchall()
            return tuple(self._item(owner_id, row) for row in rows)
        except ResourceNotFound:
            raise
        except Exception as error:
            raise ResourceUnavailable("position resources unavailable") from error

    def artifacts_for_position(self, owner_id: UUID, position_id: UUID) -> tuple[PositionArtifactItem, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position resource identifiers required")
        query = (
            "select linked.artifact_id, version.attachment_id, artifact.conversation_id as source_conversation_id, "
            "(select message.turn_id from platform_attachments.bindings binding "
            "join platform_control.conversation_messages message "
            "on message.conversation_id=binding.conversation_id and message.message_id=binding.message_id "
            "where binding.attachment_id=version.attachment_id and binding.kind='message_output' "
            "order by binding.created_at desc limit 1) as source_turn_id, "
            "version.version_no as artifact_version, version.created_at, "
            "attachment.state as attachment_state,attachment.detected_mime,attachment.declared_mime,"
            "attachment.size_bytes as attachment_size_bytes,attachment.created_at as attachment_created_at,"
            "case when version.result_status='failed' then 'failed' "
            "when exists(select 1 from platform_attachments.erasure_jobs erasure where "
            "erasure.attachment_id=version.attachment_id) then 'erasure_pending' "
            "when version.retained_until<=now() or attachment.retained_until<=now() then 'expired' "
            "when version.state='ready' and (version.immutable_locator is null or "
            "attachment.immutable_locator is null) then 'unavailable' else version.state end as resource_state, "
            "version.result_status,"
            "(version.state='ready' and version.result_status='succeeded' and "
            "version.retained_until>now() and version.immutable_locator is not null and "
            "attachment.state='ready' and attachment.retained_until>now() and "
            "attachment.immutable_locator is not null and not exists(select 1 from "
            "platform_attachments.erasure_jobs erasure where erasure.attachment_id=version.attachment_id)) "
            "as download_available,"
            "exists(select 1 from platform_attachments.derivatives derivative where "
            "derivative.attachment_id=version.attachment_id and derivative.state='ready' and "
            "derivative.retained_until>now() and "
            "derivative.kind in ('thumbnail','preview')) as preview_available "
            "from platform_hr.position_artifacts linked "
            "join platform_attachments.artifacts artifact using (artifact_id) "
            "join platform_attachments.artifact_versions version using (artifact_id) "
            "join platform_attachments.attachments attachment on attachment.attachment_id=version.attachment_id "
            "where linked.owner_internal_user_id=%s and linked.position_id=%s "
            "order by version.created_at desc,version.version_no desc"
        )
        try:
            with self._connection() as connection:
                rows = connection.execute(query, (owner_id, position_id)).fetchall()
            return tuple(self._item(
                owner_id, row, artifact_id=row["artifact_id"], artifact_version=row["artifact_version"],
            ) for row in rows)
        except ResourceNotFound:
            raise
        except Exception as error:
            raise ResourceUnavailable("position resources unavailable") from error


def _ticket(value) -> PositionResourceTicket:
    if isinstance(value, PositionResourceTicket):
        return value
    content_path = getattr(value, "content_path", None)
    expires_at = getattr(value, "expires_at", None)
    if isinstance(value, dict):
        content_path = value.get("content_path", content_path)
        expires_at = value.get("expires_at", expires_at)
    return PositionResourceTicket(content_path=content_path, expires_at=expires_at)


class HrPositionResourceService:
    def __init__(self, repository: PositionResourceRepository, tickets: AttachmentTicketService) -> None:
        for target, methods, message in (
            (repository, ("position_exists", "materials_for_position", "artifacts_for_position"), "position resource repository required"),
            (tickets, ("issue_ticket",), "position resource ticket service required"),
        ):
            if any(not callable(getattr(target, method, None)) for method in methods):
                raise ValueError(message)
        self._repository = repository
        self._tickets = tickets

    def for_position(self, owner_id: UUID, position_id: UUID) -> PositionResources:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise ValueError("position resource identifiers required")
        try:
            if not self._repository.position_exists(owner_id, position_id):
                raise ResourceNotFound("position resource not found")
            materials = self._repository.materials_for_position(owner_id, position_id)
            artifacts = self._repository.artifacts_for_position(owner_id, position_id)
        except ResourceNotFound:
            raise
        except Exception as error:  # storage and database errors intentionally remain opaque
            raise ResourceUnavailable("position resources unavailable") from error
        return PositionResources(tuple(materials), tuple(artifacts))

    def ticket(
        self,
        owner_id: UUID,
        position_id: UUID,
        attachment_id: UUID,
        purpose: Literal["preview", "download"],
    ) -> PositionResourceTicket:
        if not all(isinstance(value, UUID) for value in (owner_id, position_id, attachment_id)):
            raise ValueError("position resource identifiers required")
        if purpose not in {"preview", "download"}:
            raise ValueError("position resource purpose invalid")
        resources = self.for_position(owner_id, position_id)
        item = next((value for value in (*resources.materials, *resources.artifacts) if value.attachment_id == attachment_id), None)
        if item is None or not item.download_available or (purpose == "preview" and not item.preview_available):
            raise ResourceNotFound("position resource not found")
        try:
            return _ticket(self._tickets.issue_ticket(owner_id, attachment_id, purpose))
        except ResourceNotFound:
            raise
        except Exception as error:  # ticket service must not reveal attachment ownership or storage detail
            raise ResourceUnavailable("position resource unavailable") from error
