from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.models import PositionDraftRecord, PositionRecord
from app.hr.service import HrPositionService


@pytest.fixture
def position_record() -> PositionRecord:
    now = datetime.now(UTC)
    return PositionRecord(
        uuid4(), uuid4(), "manual", None, "结构工程师", None, (), None,
        "active", None, 1, now, now,
    )


@pytest.fixture
def draft_record() -> PositionDraftRecord:
    now = datetime.now(UTC)
    return PositionDraftRecord(
        uuid4(), uuid4(), "new_conversation", "conversation:test", None,
        "结构工程师", {}, {"message_seq": 1}, "interactive-v1", "proposed",
        None, 1, now, now,
    )


class RecordingRepository:
    def __init__(self, position: PositionRecord, draft: PositionDraftRecord) -> None:
        self.position = position
        self.draft = draft
        self.commands = []

    def create_manual(self, command):
        self.commands.append(command)
        return self.position

    def propose_draft(self, command):
        self.commands.append(command)
        return self.draft

    def confirm_draft(self, command):
        self.commands.append(command)
        return self.position

    def merge_draft(self, command):
        self.commands.append(command)
        return self.draft

    def dismiss_draft(self, command):
        self.commands.append(command)
        return self.draft

    def bind_conversation(self, command):
        self.commands.append(command)
        return command

    def correct_conversation_binding(self, command):
        self.commands.append(command)
        return command


def test_service_builds_manual_position_command(position_record, draft_record) -> None:
    generated_position = uuid4()
    repository = RecordingRepository(position_record, draft_record)
    service = HrPositionService(repository, uuid_factory=lambda: generated_position)
    owner_id = uuid4()
    request_id = uuid4()

    assert service.create_manual(
        owner_id, request_id, " 结构工程师 ", "研发", ("深圳",)
    ) is position_record
    command = repository.commands[-1]
    assert command.owner_id == owner_id
    assert command.client_request_id == request_id
    assert command.position_id == generated_position
    assert command.title == "结构工程师"


def test_service_builds_evidence_backed_draft_command(position_record, draft_record) -> None:
    generated_draft = uuid4()
    repository = RecordingRepository(position_record, draft_record)
    service = HrPositionService(repository, uuid_factory=lambda: generated_draft)
    owner_id = uuid4()
    request_id = uuid4()
    conversation_id = uuid4()

    assert service.propose_draft(
        owner_id=owner_id, request_id=request_id,
        source_kind="new_conversation", source_key=f"conversation:{conversation_id}",
        source_conversation_id=conversation_id, title="算法工程师",
        proposal={"mission": "感知"}, evidence={"message_seq": 1},
        discovery_rule_version="interactive-v1",
    ) is draft_record
    command = repository.commands[-1]
    assert command.draft_id == generated_draft
    assert command.source_conversation_id == conversation_id


def test_service_confirmation_carries_optimistic_version(position_record, draft_record) -> None:
    generated_position = uuid4()
    repository = RecordingRepository(position_record, draft_record)
    service = HrPositionService(repository, uuid_factory=lambda: generated_position)
    owner_id = uuid4()
    request_id = uuid4()

    assert service.confirm_draft(
        owner_id, draft_record.draft_id, request_id, expected_row_version=3
    ) is position_record
    command = repository.commands[-1]
    assert command.position_id == generated_position
    assert command.expected_row_version == 3


def test_service_builds_complete_draft_lifecycle_commands(position_record, draft_record) -> None:
    repository = RecordingRepository(position_record, draft_record)
    service = HrPositionService(repository)
    owner_id, draft_id, target_id, request_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )

    service.merge_draft(
        owner_id, draft_id, target_id, request_id, expected_row_version=4
    )
    assert repository.commands[-1].target_position_id == target_id
    service.dismiss_draft(owner_id, draft_id, request_id, expected_row_version=5)
    assert repository.commands[-1].expected_row_version == 5


def test_service_builds_binding_and_audited_correction_commands(
    position_record, draft_record
) -> None:
    repository = RecordingRepository(position_record, draft_record)
    service = HrPositionService(repository)
    owner_id, conversation_id, previous_id, new_id, request_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )

    service.bind_conversation(
        owner_id, new_id, conversation_id, request_id,
        binding_kind="created_in_position",
    )
    assert repository.commands[-1].conversation_id == conversation_id
    service.correct_conversation_binding(
        owner_id, conversation_id, previous_id, new_id, request_id,
        reason="人工确认岗位归属",
    )
    command = repository.commands[-1]
    assert command.previous_position_id == previous_id
    assert command.reason == "人工确认岗位归属"
