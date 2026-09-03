from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from .models import (
    ConfirmPositionDraft,
    CreateManualPosition,
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


class HrPositionService:
    def __init__(
        self,
        repository: PositionCommandRepository,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        for method in ("create_manual", "propose_draft", "confirm_draft"):
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
