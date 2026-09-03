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
    PositionRecord,
    ProposePositionDraft,
)


class PositionCommandRepository(Protocol):
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


class HrPositionService:
    def __init__(
        self,
        repository: PositionCommandRepository,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        for method in (
            "create_manual",
            "propose_draft",
            "confirm_draft",
            "merge_draft",
            "dismiss_draft",
            "bind_conversation",
            "correct_conversation_binding",
        ):
            if not callable(getattr(repository, method, None)):
                raise ValueError("HR position repository invalid")
        if not callable(uuid_factory):
            raise ValueError("HR UUID factory invalid")
        self._repository = repository
        self._uuid_factory = uuid_factory

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
