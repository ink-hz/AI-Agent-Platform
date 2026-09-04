from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from .position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
    PositionContextVersion,
)


class PositionIntelligenceCommands(Protocol):
    def current(self, owner_id: UUID, position_id: UUID) -> PositionContextVersion | None: ...
    def list_versions(self, owner_id: UUID, position_id: UUID, *, state: str | None = None) -> tuple[PositionContextVersion, ...]: ...
    def create_draft(self, command: CreateContextDraft) -> PositionContextVersion: ...
    def confirm_modules(self, command: ConfirmContextModules) -> PositionContextVersion: ...
    def compare(self, owner_id: UUID, position_id: UUID, left: UUID, right: UUID) -> dict[str, object]: ...
    def official_versions(self, owner_id: UUID, position_id: UUID) -> tuple[object, ...]: ...


class PositionIntelligenceService:
    def __init__(
        self,
        repository: PositionIntelligenceCommands,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        for name in (
            "current", "list_versions", "create_draft", "confirm_modules",
            "compare", "official_versions",
        ):
            if not callable(getattr(repository, name, None)):
                raise ValueError("position intelligence repository invalid")
        if not callable(uuid_factory):
            raise ValueError("position intelligence UUID factory invalid")
        self._repository = repository
        self._uuid_factory = uuid_factory

    def current(self, owner_id: UUID, position_id: UUID) -> PositionContextVersion | None:
        return self._repository.current(owner_id, position_id)

    def history(self, owner_id: UUID, position_id: UUID) -> tuple[PositionContextVersion, ...]:
        return self._repository.list_versions(owner_id, position_id)

    def drafts(self, owner_id: UUID, position_id: UUID) -> tuple[PositionContextVersion, ...]:
        return self._repository.list_versions(owner_id, position_id, state="draft")

    def official_versions(self, owner_id: UUID, position_id: UUID) -> tuple[object, ...]:
        return self._repository.official_versions(owner_id, position_id)

    def create_draft(
        self,
        *,
        owner_id: UUID,
        position_id: UUID,
        request_id: UUID,
        base_context_version_id: UUID | None,
        official_version_id: UUID | None,
        modules: dict[str, object],
        summary: str,
        source_conversation_id: UUID | None = None,
        source_turn_id: UUID | None = None,
        source_artifact_version_id: UUID | None = None,
        source_material_attachment_ids: tuple[UUID, ...] = (),
        agent_id: str | None = None,
        model_version: str | None = None,
        created_by: UUID | None = None,
    ) -> PositionContextVersion:
        return self._repository.create_draft(CreateContextDraft(
            owner_id=owner_id,
            context_version_id=self._uuid_factory(),
            position_id=position_id,
            base_context_version_id=base_context_version_id,
            official_version_id=official_version_id,
            modules=modules,
            summary=summary,
            client_request_id=request_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            source_artifact_version_id=source_artifact_version_id,
            source_material_attachment_ids=source_material_attachment_ids,
            agent_id=agent_id,
            model_version=model_version,
            created_by=created_by,
        ))

    def confirm_modules(
        self,
        *,
        owner_id: UUID,
        position_id: UUID,
        draft_context_version_id: UUID,
        request_id: UUID,
        expected_current_context_version_id: UUID | None,
        expected_draft_row_version: int,
        module_names: tuple[str, ...],
        confirmed_by: UUID,
    ) -> PositionContextVersion:
        return self._repository.confirm_modules(ConfirmContextModules(
            owner_id=owner_id,
            position_id=position_id,
            draft_context_version_id=draft_context_version_id,
            client_request_id=request_id,
            expected_current_context_version_id=expected_current_context_version_id,
            expected_draft_row_version=expected_draft_row_version,
            module_names=module_names,
            confirmed_by=confirmed_by,
        ))

    def compare(
        self, owner_id: UUID, position_id: UUID, left: UUID, right: UUID
    ) -> dict[str, object]:
        return self._repository.compare(owner_id, position_id, left, right)
