from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from .models import (
    BindPositionConversation,
    ConfirmedPositionPackage,
    ConfirmPositionDraft,
    CorrectPositionConversationBinding,
    CreateManualPosition,
    CreatePositionDraftVersion,
    DismissPositionDraft,
    MergePositionDraft,
    PositionConversationBinding,
    PositionDraftRecord,
    PositionDraftVersion,
    PositionMaterialRecord,
    PositionRecord,
    PromotePositionMaterial,
    ProposePositionDraft,
)
from .repository import PositionPage


class PositionCommandRepository(Protocol):
    def list_positions(self, owner_id: UUID, **filters) -> PositionPage: ...

    def position_for_owner(
        self, owner_id: UUID, position_id: UUID
    ) -> object: ...

    def list_drafts(
        self, owner_id: UUID, *, state: str | None = None, limit: int = 100
    ) -> tuple[PositionDraftRecord, ...]: ...

    def create_manual(self, command: CreateManualPosition) -> PositionRecord: ...

    def propose_draft(
        self, command: ProposePositionDraft
    ) -> PositionDraftRecord: ...

    def confirm_draft(self, command: ConfirmPositionDraft) -> PositionRecord: ...

    def create_draft_version(
        self, command: CreatePositionDraftVersion
    ) -> PositionDraftVersion: ...

    def latest_draft_version(
        self, owner_id: UUID, draft_id: UUID
    ) -> PositionDraftVersion: ...

    def position_package_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[PositionDraftRecord, PositionDraftVersion]: ...

    def confirm_package(
        self, owner_id: UUID, draft_id: UUID, draft_version_id: UUID,
        request_id: UUID, *, expected_row_version: int,
    ) -> ConfirmedPositionPackage: ...

    def merge_draft(self, command: MergePositionDraft) -> PositionDraftRecord: ...

    def dismiss_draft(self, command: DismissPositionDraft) -> PositionDraftRecord: ...

    def bind_conversation(
        self, command: BindPositionConversation
    ) -> PositionConversationBinding: ...

    def correct_conversation_binding(
        self, command: CorrectPositionConversationBinding
    ) -> PositionConversationBinding: ...

    def promote_material(self, command: PromotePositionMaterial) -> PositionMaterialRecord: ...

    def remove_material(
        self, owner_id: UUID, position_id: UUID, attachment_id: UUID,
        client_request_id: UUID,
    ) -> PositionMaterialRecord: ...


class HrPositionService:
    def __init__(
        self,
        repository: PositionCommandRepository,
        *,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        for method in (
            "list_positions",
            "position_for_owner",
            "list_drafts",
            "create_manual",
            "propose_draft",
            "confirm_draft",
            "create_draft_version",
            "latest_draft_version",
            "position_package_for_conversation",
            "confirm_package",
            "merge_draft",
            "dismiss_draft",
            "bind_conversation",
            "correct_conversation_binding",
            "promote_material",
            "remove_material",
        ):
            if not callable(getattr(repository, method, None)):
                raise ValueError("HR position repository invalid")
        if uuid_factory is not None and not callable(uuid_factory):
            raise ValueError("HR UUID factory invalid")
        self._repository = repository
        self._uuid_factory = uuid_factory

    def _new_id(self) -> UUID:
        if self._uuid_factory is not None:
            return self._uuid_factory()
        return uuid4()

    def _resource_id(
        self, owner_id: UUID, request_id: UUID, operation: str
    ) -> UUID:
        if self._uuid_factory is not None:
            return self._uuid_factory()
        return uuid5(owner_id, f"hr-position:{operation}:{request_id}")

    def list_positions(self, owner_id: UUID, **filters) -> PositionPage:
        return self._repository.list_positions(owner_id, **filters)

    def position(self, owner_id: UUID, position_id: UUID):
        return self._repository.position_for_owner(owner_id, position_id)

    def list_drafts(
        self, owner_id: UUID, *, state: str | None = None, limit: int = 100
    ) -> tuple[PositionDraftRecord, ...]:
        return self._repository.list_drafts(owner_id, state=state, limit=limit)

    def create_manual(
        self,
        owner_id: UUID,
        request_id: UUID,
        title: str,
        department: str | None = None,
        locations: tuple[str, ...] = (),
    ) -> PositionRecord:
        return self._repository.create_manual(
            CreateManualPosition(
                owner_id=owner_id,
                position_id=self._new_id(),
                client_request_id=request_id,
                title=title,
                department=department,
                locations=locations,
            )
        )

    def propose_draft(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        source_kind: str,
        source_key: str,
        source_conversation_id: UUID | None,
        title: str,
        proposal: dict[str, object],
        evidence: dict[str, object],
        discovery_rule_version: str,
    ) -> PositionDraftRecord:
        return self._repository.propose_draft(
            ProposePositionDraft(
                owner_id=owner_id,
                draft_id=self._new_id(),
                client_request_id=request_id,
                source_kind=source_kind,
                source_key=source_key,
                source_conversation_id=source_conversation_id,
                title=title,
                proposal=proposal,
                evidence=evidence,
                discovery_rule_version=discovery_rule_version,
            )
        )

    def confirm_draft(
        self,
        owner_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        *,
        expected_row_version: int,
    ) -> PositionRecord:
        return self._repository.confirm_draft(
            ConfirmPositionDraft(
                owner_id=owner_id,
                draft_id=draft_id,
                position_id=self._new_id(),
                client_request_id=request_id,
                expected_row_version=expected_row_version,
            )
        )

    def create_draft_version(
        self,
        *,
        owner_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        title: str,
        modules: dict[str, object],
        source_conversation_id: UUID,
        source_turn_id: UUID,
        source_assistant_message_id: UUID,
        agent_id: str,
        model_version: str,
    ) -> PositionDraftVersion:
        return self._repository.create_draft_version(
            CreatePositionDraftVersion(
                owner_id=owner_id,
                draft_version_id=self._resource_id(
                    owner_id, request_id, "draft-version"
                ),
                draft_id=draft_id,
                client_request_id=request_id,
                title=title,
                modules=modules,
                source_conversation_id=source_conversation_id,
                source_turn_id=source_turn_id,
                source_assistant_message_id=source_assistant_message_id,
                agent_id=agent_id,
                model_version=model_version,
            )
        )

    def latest_draft_version(
        self, owner_id: UUID, draft_id: UUID
    ) -> PositionDraftVersion:
        return self._repository.latest_draft_version(owner_id, draft_id)

    def position_package_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[PositionDraftRecord, PositionDraftVersion]:
        return self._repository.position_package_for_conversation(
            owner_id, conversation_id
        )

    def confirm_package(
        self,
        owner_id: UUID,
        draft_id: UUID,
        draft_version_id: UUID,
        request_id: UUID,
        *,
        expected_row_version: int,
    ) -> ConfirmedPositionPackage:
        return self._repository.confirm_package(
            owner_id, draft_id, draft_version_id, request_id,
            expected_row_version=expected_row_version,
        )

    def merge_draft(
        self,
        owner_id: UUID,
        draft_id: UUID,
        target_position_id: UUID,
        request_id: UUID,
        *,
        expected_row_version: int,
    ) -> PositionDraftRecord:
        return self._repository.merge_draft(
            MergePositionDraft(
                owner_id,
                draft_id,
                target_position_id,
                request_id,
                expected_row_version,
            )
        )

    def dismiss_draft(
        self,
        owner_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        *,
        expected_row_version: int,
    ) -> PositionDraftRecord:
        return self._repository.dismiss_draft(
            DismissPositionDraft(
                owner_id, draft_id, request_id, expected_row_version
            )
        )

    def bind_conversation(
        self,
        owner_id: UUID,
        position_id: UUID,
        conversation_id: UUID,
        request_id: UUID,
        *,
        binding_kind: str,
    ) -> PositionConversationBinding:
        return self._repository.bind_conversation(
            BindPositionConversation(
                owner_id,
                position_id,
                conversation_id,
                request_id,
                binding_kind,
            )
        )

    def correct_conversation_binding(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        previous_position_id: UUID,
        new_position_id: UUID,
        request_id: UUID,
        *,
        reason: str,
    ) -> PositionConversationBinding:
        return self._repository.correct_conversation_binding(
            CorrectPositionConversationBinding(
                owner_id,
                conversation_id,
                previous_position_id,
                new_position_id,
                request_id,
                reason,
            )
        )

    def promote_material(
        self,
        owner_id: UUID,
        position_id: UUID,
        attachment_id: UUID,
        request_id: UUID,
    ) -> PositionMaterialRecord:
        return self._repository.promote_material(PromotePositionMaterial(
            owner_id, position_id, attachment_id, request_id
        ))

    def remove_material(
        self,
        owner_id: UUID,
        position_id: UUID,
        attachment_id: UUID,
        request_id: UUID,
    ) -> PositionMaterialRecord:
        return self._repository.remove_material(
            owner_id, position_id, attachment_id, request_id
        )
