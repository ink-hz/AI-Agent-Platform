"""Public, storage-safe projections for resources explicitly linked to an HR position."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


def _identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("position resource identifier invalid")
    return value


def _text(value: str, message: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(message)
    return value


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("position resource timestamp invalid")
    return value


@dataclass(frozen=True, slots=True)
class PositionMaterialItem:
    attachment_id: UUID
    filename: str
    media_type: str
    state: str
    size_bytes: int
    created_at: datetime
    source_conversation_id: UUID | None
    source_turn_id: UUID | None
    preview_available: bool
    download_available: bool

    def __post_init__(self) -> None:
        _identifier(self.attachment_id)
        _text(self.filename, "position resource filename invalid")
        _text(self.media_type, "position resource media type invalid")
        _text(self.state, "position resource state invalid")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("position resource size invalid")
        _time(self.created_at)
        if self.source_conversation_id is not None:
            _identifier(self.source_conversation_id)
        if self.source_turn_id is not None:
            _identifier(self.source_turn_id)
        if type(self.preview_available) is not bool or type(self.download_available) is not bool:
            raise ValueError("position resource capability invalid")


@dataclass(frozen=True, slots=True)
class PositionArtifactItem:
    artifact_id: UUID
    attachment_id: UUID
    artifact_version: int
    filename: str
    media_type: str
    state: str
    size_bytes: int
    created_at: datetime
    source_conversation_id: UUID | None
    source_turn_id: UUID | None
    preview_available: bool
    download_available: bool

    def __post_init__(self) -> None:
        _identifier(self.artifact_id)
        _identifier(self.attachment_id)
        if isinstance(self.artifact_version, bool) or not isinstance(self.artifact_version, int) or self.artifact_version < 1:
            raise ValueError("position artifact version invalid")
        PositionMaterialItem(
            self.attachment_id, self.filename, self.media_type, self.state,
            self.size_bytes, self.created_at, self.source_conversation_id,
            self.source_turn_id, self.preview_available, self.download_available,
        )


@dataclass(frozen=True, slots=True)
class PositionResources:
    materials: tuple[PositionMaterialItem, ...]
    artifacts: tuple[PositionArtifactItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.materials, tuple) or not isinstance(self.artifacts, tuple):
            raise ValueError("position resources invalid")
        if any(not isinstance(value, PositionMaterialItem) for value in self.materials):
            raise ValueError("position resources invalid")
        if any(not isinstance(value, PositionArtifactItem) for value in self.artifacts):
            raise ValueError("position resources invalid")


@dataclass(frozen=True, slots=True)
class PositionResourceTicket:
    content_path: str
    expires_at: datetime | str

    def __post_init__(self) -> None:
        _text(self.content_path, "position resource ticket invalid")
        if not isinstance(self.expires_at, (datetime, str)):
            raise ValueError("position resource ticket invalid")
