from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid5

from .position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
    CreatePositionTaskRequest,
    PositionContextVersion,
)


class PositionIntelligenceCommands(Protocol):
    def current(self, owner_id: UUID, position_id: UUID) -> PositionContextVersion | None: ...
    def list_versions(self, owner_id: UUID, position_id: UUID, *, state: str | None = None) -> tuple[PositionContextVersion, ...]: ...
    def create_draft(self, command: CreateContextDraft) -> PositionContextVersion: ...
    def confirm_modules(self, command: ConfirmContextModules) -> PositionContextVersion: ...
    def compare(self, owner_id: UUID, position_id: UUID, left: UUID, right: UUID) -> dict[str, object]: ...
    def official_versions(self, owner_id: UUID, position_id: UUID) -> tuple[object, ...]: ...
    def official_version(self, owner_id: UUID, position_id: UUID, official_version_id: UUID) -> object: ...
    def create_task_request(self, command: CreatePositionTaskRequest) -> object: ...
    def task_request(self, owner_id: UUID, position_id: UUID, client_request_id: UUID) -> object | None: ...


class PositionIntelligenceService:
    def __init__(
        self,
        repository: PositionIntelligenceCommands,
        *,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        for name in (
            "current", "list_versions", "create_draft", "confirm_modules",
            "compare", "official_versions", "official_version",
            "create_task_request", "task_request",
        ):
            if not callable(getattr(repository, name, None)):
                raise ValueError("position intelligence repository invalid")
        if uuid_factory is not None and not callable(uuid_factory):
            raise ValueError("position intelligence UUID factory invalid")
        self._repository = repository
        self._uuid_factory = uuid_factory

    def _resource_id(self, owner_id: UUID, request_id: UUID, operation: str) -> UUID:
        if self._uuid_factory is not None:
            return self._uuid_factory()
        return uuid5(owner_id, f"position-intelligence:{operation}:{request_id}")

    def current(self, owner_id: UUID, position_id: UUID) -> PositionContextVersion | None:
        return self._repository.current(owner_id, position_id)

    def history(self, owner_id: UUID, position_id: UUID) -> tuple[PositionContextVersion, ...]:
        return self._repository.list_versions(owner_id, position_id)

    def drafts(self, owner_id: UUID, position_id: UUID) -> tuple[PositionContextVersion, ...]:
        return self._repository.list_versions(owner_id, position_id, state="draft")

    def official_versions(self, owner_id: UUID, position_id: UUID) -> tuple[object, ...]:
        return self._repository.official_versions(owner_id, position_id)

    def official_version(
        self, owner_id: UUID, position_id: UUID, official_version_id: UUID
    ) -> object:
        return self._repository.official_version(
            owner_id, position_id, official_version_id
        )

    def create_task_request(
        self,
        *,
        owner_id: UUID,
        position_id: UUID,
        request_id: UUID,
        canonical_payload_sha256: str,
        task_kind: str,
        expected_context_version_id: UUID | None,
        material_attachment_ids: tuple[UUID, ...] = (),
        candidate_id: UUID | None = None,
        position_candidate_id: UUID | None = None,
    ) -> object:
        return self._repository.create_task_request(CreatePositionTaskRequest(
            task_request_id=self._resource_id(owner_id, request_id, "task-request"),
            owner_id=owner_id,
            position_id=position_id,
            client_request_id=request_id,
            canonical_payload_sha256=canonical_payload_sha256,
            task_kind=task_kind,
            expected_context_version_id=expected_context_version_id,
            material_attachment_ids=material_attachment_ids,
            candidate_id=candidate_id,
            position_candidate_id=position_candidate_id,
        ))

    def task_request(
        self, owner_id: UUID, position_id: UUID, request_id: UUID
    ) -> object | None:
        return self._repository.task_request(owner_id, position_id, request_id)

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
            context_version_id=self._resource_id(owner_id, request_id, "context-draft"),
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
