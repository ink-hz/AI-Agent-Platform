from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from .candidate_models import (
    CandidateDraft,
    CompleteCandidateDraft,
    CreateCandidateDraftBatch,
    FailCandidateDraft,
    RetryCandidateDraft,
)


class ResumeBatchStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResumeBatch:
    batch_id: UUID
    owner_id: UUID
    position_id: UUID
    items: tuple[CandidateDraft, ...]


class ResumeBatchCoordinator:
    def __init__(self, candidate_service) -> None:
        required = (
            "create_drafts", "draft", "list_drafts", "start_draft",
            "complete_draft", "fail_draft", "retry_draft",
        )
        if any(not callable(getattr(candidate_service, name, None)) for name in required):
            raise ValueError("candidate service required")
        self._service = candidate_service
        self._batches: dict[UUID, tuple[UUID, UUID, tuple[UUID, ...]]] = {}
        self._draft_scope: dict[UUID, tuple[UUID, UUID]] = {}

    def enqueue(self, command: CreateCandidateDraftBatch) -> ResumeBatch:
        if not isinstance(command, CreateCandidateDraftBatch):
            raise ValueError("resume batch command required")
        items = self._service.create_drafts(command)
        order = tuple(item.draft_id for item in items)
        self._batches[command.client_request_id] = (
            command.owner_id, command.position_id, order
        )
        for item in items:
            self._draft_scope[item.draft_id] = (item.owner_id, item.position_id)
        return ResumeBatch(
            command.client_request_id, command.owner_id, command.position_id, items
        )

    def read(self, batch_id: UUID) -> ResumeBatch:
        try:
            owner_id, position_id, order = self._batches[batch_id]
        except KeyError:
            raise ResumeBatchStateError("resume batch scope unavailable") from None
        items = self._service.list_drafts(
            owner_id, position_id, batch_request_id=batch_id
        )
        by_id = {item.draft_id: item for item in items}
        if set(by_id) != set(order):
            raise ResumeBatchStateError("resume batch projection incomplete")
        return ResumeBatch(
            batch_id, owner_id, position_id, tuple(by_id[value] for value in order)
        )

    def read_for_owner(
        self, owner_id: UUID, position_id: UUID, batch_id: UUID
    ) -> ResumeBatch:
        items = self._service.list_drafts(
            owner_id, position_id, batch_request_id=batch_id
        )
        if not items:
            raise ResumeBatchStateError("resume batch not found")
        order = tuple(item.draft_id for item in items)
        self._batches[batch_id] = (owner_id, position_id, order)
        for item in items:
            self._draft_scope[item.draft_id] = (owner_id, position_id)
        return ResumeBatch(batch_id, owner_id, position_id, items)

    def _draft(self, draft_id: UUID) -> CandidateDraft:
        try:
            owner_id, _ = self._draft_scope[draft_id]
        except KeyError:
            raise ResumeBatchStateError("resume draft scope unavailable") from None
        return self._service.draft(owner_id, draft_id)

    @staticmethod
    def _request(draft_id: UUID, operation: str, row_version: int) -> UUID:
        return uuid5(draft_id, f"{operation}:{row_version}")

    def _start_pending(self, draft: CandidateDraft) -> CandidateDraft:
        if draft.state == "processing":
            return draft
        if draft.state != "pending":
            raise ResumeBatchStateError("resume draft requires explicit retry")
        return self._service.start_draft(
            draft.owner_id, draft.draft_id,
            self._request(draft.draft_id, "start", draft.row_version),
            draft.row_version,
        )

    def complete_item(
        self,
        draft_id: UUID,
        extracted_facts: dict[str, object],
        identity_candidates: tuple[UUID, ...] = (),
    ) -> CandidateDraft:
        draft = self._start_pending(self._draft(draft_id))
        return self._service.complete_draft(CompleteCandidateDraft(
            draft.owner_id, draft.draft_id,
            self._request(draft.draft_id, "complete", draft.row_version),
            draft.row_version, extracted_facts, identity_candidates,
        ))

    def fail_item(self, draft_id: UUID, error_code: str) -> CandidateDraft:
        draft = self._start_pending(self._draft(draft_id))
        return self._service.fail_draft(FailCandidateDraft(
            draft.owner_id, draft.draft_id,
            self._request(draft.draft_id, "fail", draft.row_version),
            draft.row_version, error_code,
        ))

    def retry_item(
        self, draft_id: UUID, *, request_id: UUID, expected_row_version: int
    ) -> CandidateDraft:
        draft = self._draft(draft_id)
        if draft.state != "failed":
            raise ResumeBatchStateError("only failed resume drafts can retry")
        return self._service.retry_draft(
            RetryCandidateDraft(
                owner_id=draft.owner_id,
                draft_id=draft_id,
                client_request_id=request_id,
                expected_row_version=expected_row_version,
            )
        )
