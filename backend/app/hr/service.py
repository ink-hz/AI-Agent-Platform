from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from .models import (
    BindPositionConversation,
    ConfirmPositionDraft,
    CorrectPositionConversationBinding,
    CreateManualPosition,
    DismissPositionDraft,
    MergePositionDraft,
    PositionConversationBinding,
    PositionDraftRecord,
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
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        for method in (
            "list_positions",
            "position_for_owner",
            "list_drafts",
            "create_manual",
            "propose_draft",
            "confirm_draft",
            "merge_draft",
            "dismiss_draft",
            "bind_conversation",
            "correct_conversation_binding",
            "promote_material",
            "remove_material",
        ):
            if not callable(getattr(repository, method, None)):
                raise ValueError("HR position repository invalid")
        if not callable(uuid_factory):
            raise ValueError("HR UUID factory invalid")
        self._repository = repository
        self._uuid_factory = uuid_factory

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
                position_id=self._uuid_factory(),
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
                draft_id=self._uuid_factory(),
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
                position_id=self._uuid_factory(),
                client_request_id=request_id,
                expected_row_version=expected_row_version,
            )
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
