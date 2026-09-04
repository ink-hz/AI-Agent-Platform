from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4, uuid5

import pytest

from app.hr.models import (
    ConfirmedPositionPackage,
    PositionDraftRecord,
    PositionDraftVersion,
    PositionRecord,
)
from app.hr.position_intelligence_models import PositionContextVersion
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

    def list_positions(self, owner_id, **filters):
        self.commands.append((owner_id, filters))
        return ()

    def position_for_owner(self, owner_id, position_id):
        self.commands.append((owner_id, position_id))
        return self.position

    def list_drafts(self, owner_id, *, state=None, limit=100):
        self.commands.append((owner_id, state, limit))
        return (self.draft,)

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

    def promote_material(self, command):
        self.commands.append(command)
        return command

    def remove_material(self, *args):
        self.commands.append(args)
        return args

    def create_draft_version(self, command):
        self.commands.append(command)
        return self.draft_version

    def latest_draft_version(self, owner_id, draft_id):
        self.commands.append((owner_id, draft_id))
        return self.draft_version

    def position_package_for_conversation(self, owner_id, conversation_id):
        self.commands.append((owner_id, conversation_id))
        return self.draft, self.draft_version

    def confirm_package(self, *args, **kwargs):
        self.commands.append((args, kwargs))
        return self.confirmed_package


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


def test_service_creates_and_confirms_a_versioned_position_package(
    position_record, draft_record
) -> None:
    now = datetime.now(UTC)
    owner_id = draft_record.owner_id
    position_record = replace(position_record, owner_id=owner_id)
    conversation_id, turn_id, message_id = uuid4(), uuid4(), uuid4()
    draft_version_id = uuid4()
    repository = RecordingRepository(position_record, draft_record)
    repository.draft_version = PositionDraftVersion(
        draft_version_id, owner_id, draft_record.draft_id, uuid4(), 1,
        "高级结构工程师",
        {"mission": {"text": "M"}, "jd": {"text": "JD"}, "jr": {"text": "JR"}},
        conversation_id, turn_id, message_id, "hr-bot", "gpt-5", 1, now, now,
    )
    context = PositionContextVersion(
        uuid4(), owner_id, position_record.position_id, 1, "confirmed",
        repository.draft_version.modules, "高级结构工程师", None, None,
        conversation_id, turn_id, None, (), "hr-bot", "gpt-5", owner_id,
        owner_id, now, now, 1,
    )
    repository.confirmed_package = ConfirmedPositionPackage(
        position_record, context, conversation_id
    )
    service = HrPositionService(repository, uuid_factory=lambda: draft_version_id)
    create_request = uuid4()

    assert service.create_draft_version(
        owner_id=owner_id, draft_id=draft_record.draft_id,
        request_id=create_request, title=" 高级结构工程师 ",
        modules={"mission": {"text": "M"}, "jd": {"text": "JD"}, "jr": {"text": "JR"}},
        source_conversation_id=conversation_id, source_turn_id=turn_id,
        source_assistant_message_id=message_id, agent_id="hr-bot",
        model_version="gpt-5",
    ) is repository.draft_version
    command = repository.commands[-1]
    assert command.draft_version_id == draft_version_id
    assert command.client_request_id == create_request

    confirm_request = uuid4()
    assert service.confirm_package(
        owner_id, draft_record.draft_id, draft_version_id, confirm_request,
        expected_row_version=1,
    ) is repository.confirmed_package
    assert repository.commands[-1] == (
        (owner_id, draft_record.draft_id, draft_version_id, confirm_request),
        {"expected_row_version": 1},
    )


def test_service_reads_position_package_by_owner_and_conversation(
    position_record, draft_record
) -> None:
    repository = RecordingRepository(position_record, draft_record)
    repository.draft_version = object()
    service = HrPositionService(repository)
    owner_id, conversation_id = uuid4(), uuid4()

    assert service.position_package_for_conversation(
        owner_id, conversation_id
    ) == (draft_record, repository.draft_version)
    assert repository.commands[-1] == (owner_id, conversation_id)


def test_service_reuses_default_draft_version_id_for_request_replay(
    position_record, draft_record
) -> None:
    repository = RecordingRepository(position_record, draft_record)
    repository.draft_version = object()
    service = HrPositionService(repository)
    owner_id, request_id = uuid4(), uuid4()
    arguments = {
        "owner_id": owner_id,
        "draft_id": draft_record.draft_id,
        "request_id": request_id,
        "title": "高级结构工程师",
        "modules": {
            "mission": {"text": "M"},
            "jd": {"text": "JD"},
            "jr": {"text": "JR"},
        },
        "source_conversation_id": uuid4(),
        "source_turn_id": uuid4(),
        "source_assistant_message_id": uuid4(),
        "agent_id": "hr-bot",
        "model_version": "gpt-5",
    }

    service.create_draft_version(**arguments)
    service.create_draft_version(**arguments)
    first, second = repository.commands[-2:]

    assert first.draft_version_id == second.draft_version_id
    assert first.draft_version_id == uuid5(
        owner_id, f"hr-position:draft-version:{request_id}"
    )


@pytest.mark.parametrize(
    "missing",
    [
        "create_draft_version",
        "latest_draft_version",
        "position_package_for_conversation",
        "confirm_package",
    ],
)
def test_service_requires_position_package_repository_capabilities(
    position_record, draft_record, missing
) -> None:
    repository = RecordingRepository(position_record, draft_record)
    setattr(repository, missing, None)

    with pytest.raises(ValueError, match="HR position repository invalid"):
        HrPositionService(repository)
