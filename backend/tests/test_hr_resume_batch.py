from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.candidate_models import CandidateDraft, CreateCandidateDraftBatch
from app.hr.resume_batch import ResumeBatchCoordinator, ResumeBatchStateError

NOW = datetime.now(UTC)


class BatchService:
    def __init__(self):
        self.drafts = {}

    def create_drafts(self, command):
        values = []
        for attachment_id in command.attachment_ids:
            draft = CandidateDraft(
                uuid4(), command.owner_id, command.position_id, attachment_id,
                command.client_request_id, uuid4(), "pending", {}, (), None,
                1, NOW, NOW,
            )
            self.drafts[draft.draft_id] = draft
            values.append(draft)
        return tuple(values)

    def draft(self, owner_id, draft_id):
        value = self.drafts[draft_id]
        assert value.owner_id == owner_id
        return value

    def list_drafts(self, owner_id, position_id, *, batch_request_id):
        return tuple(
            value for value in self.drafts.values()
            if value.owner_id == owner_id and value.position_id == position_id
            and value.batch_request_id == batch_request_id
        )

    def start_draft(self, owner_id, draft_id, request_id, expected_row_version):
        value = self.draft(owner_id, draft_id)
        value = replace(value, state="processing", row_version=value.row_version + 1)
        self.drafts[draft_id] = value
        return value

    def complete_draft(
        self, owner_id, draft_id, request_id, expected_row_version,
        extracted_facts, identity_candidates=(),
    ):
        value = self.draft(owner_id, draft_id)
        value = replace(
            value, state="ready", extracted_facts=extracted_facts,
            identity_candidates=identity_candidates,
            row_version=value.row_version + 1,
        )
        self.drafts[draft_id] = value
        return value

    def fail_draft(
        self, owner_id, draft_id, request_id, expected_row_version, error_code
    ):
        value = self.draft(owner_id, draft_id)
        value = replace(
            value, state="failed", error_code=error_code,
            row_version=value.row_version + 1,
        )
        self.drafts[draft_id] = value
        return value

    def retry_draft(self, command):
        value = self.draft(command.owner_id, command.draft_id)
        value = replace(
            value, state="pending", error_code=None,
            row_version=value.row_version + 1,
        )
        self.drafts[value.draft_id] = value
        return value


def _coordinator():
    return ResumeBatchCoordinator(BatchService())


def test_one_failed_resume_does_not_roll_back_ready_siblings() -> None:
    coordinator = _coordinator()
    command = CreateCandidateDraftBatch(
        uuid4(), uuid4(), (uuid4(), uuid4(), uuid4()), uuid4()
    )
    batch = coordinator.enqueue(command)

    coordinator.complete_item(batch.items[0].draft_id, {"stable_name": "甲"})
    coordinator.fail_item(batch.items[1].draft_id, "parse_failed")
    coordinator.complete_item(batch.items[2].draft_id, {"stable_name": "丙"})

    assert [item.state for item in coordinator.read(batch.batch_id).items] == [
        "ready", "failed", "ready"
    ]


def test_failed_item_retries_in_place_without_touching_successful_siblings() -> None:
    coordinator = _coordinator()
    command = CreateCandidateDraftBatch(
        uuid4(), uuid4(), (uuid4(), uuid4()), uuid4()
    )
    batch = coordinator.enqueue(command)
    ready_id, failed_id = batch.items[0].draft_id, batch.items[1].draft_id
    coordinator.complete_item(ready_id, {"stable_name": "甲"})
    failed = coordinator.fail_item(failed_id, "parse_failed")

    retried = coordinator.retry_item(
        failed_id, request_id=uuid4(), expected_row_version=failed.row_version
    )

    restored = coordinator.read(batch.batch_id)
    assert retried.draft_id == failed_id
    assert [item.state for item in restored.items] == ["ready", "pending"]


def test_terminal_or_failed_items_cannot_be_completed_without_explicit_retry() -> None:
    coordinator = _coordinator()
    command = CreateCandidateDraftBatch(uuid4(), uuid4(), (uuid4(),), uuid4())
    batch = coordinator.enqueue(command)
    coordinator.fail_item(batch.items[0].draft_id, "parse_failed")

    with pytest.raises(ResumeBatchStateError):
        coordinator.complete_item(batch.items[0].draft_id, {"stable_name": "甲"})
