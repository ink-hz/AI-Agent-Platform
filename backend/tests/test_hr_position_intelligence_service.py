from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.hr.position_intelligence_models import PositionContextVersion
from app.hr.position_intelligence_service import PositionIntelligenceService


def _context(owner_id, position_id, context_id):
    return PositionContextVersion(
        context_id, owner_id, position_id, 1, "draft", {"mission": {}},
        "summary", None, None, None, None, None, (), None, None, owner_id,
        None, datetime.now(UTC), None, 1,
    )


class RecordingRepository:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def current(self, *args):
        self.calls.append(("current", args))
        return self.context

    def list_versions(self, *args, **kwargs):
        self.calls.append(("list", args, kwargs))
        return (self.context,)

    def create_draft(self, command):
        self.calls.append(("draft", command))
        return self.context

    def confirm_modules(self, command):
        self.calls.append(("confirm", command))
        return self.context

    def compare(self, *args):
        self.calls.append(("compare", args))
        return {"changed_modules": ()}

    def official_versions(self, *args):
        self.calls.append(("official", args))
        return ()

    def official_version(self, *args):
        self.calls.append(("official_detail", args))
        return None

    def create_task_request(self, command):
        self.calls.append(("task_request", command))
        return command

    def task_request(self, *args):
        self.calls.append(("task_request_read", args))
        return None


def test_service_builds_draft_and_human_confirmation_commands() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository(_context(owner_id, position_id, context_id))
    generated_id = uuid4()
    service = PositionIntelligenceService(repository, uuid_factory=lambda: generated_id)

    service.create_draft(
        owner_id=owner_id, position_id=position_id, request_id=uuid4(),
        base_context_version_id=None, official_version_id=None,
        modules={"mission": {}}, summary="summary", created_by=owner_id,
    )
    draft_command = repository.calls[-1][1]
    assert draft_command.context_version_id == generated_id
    assert draft_command.created_by == owner_id

    service.confirm_modules(
        owner_id=owner_id, position_id=position_id,
        draft_context_version_id=context_id, request_id=uuid4(),
        expected_current_context_version_id=None,
        expected_draft_row_version=1, module_names=("mission",),
        confirmed_by=owner_id,
    )
    confirmation = repository.calls[-1][1]
    assert confirmation.confirmed_by == owner_id
    assert confirmation.module_names == ("mission",)


def test_service_delegates_scoped_reads_and_compare() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository(_context(owner_id, position_id, context_id))
    service = PositionIntelligenceService(repository)

    assert service.current(owner_id, position_id) is repository.context
    assert service.history(owner_id, position_id) == (repository.context,)
    assert service.compare(owner_id, position_id, context_id, context_id) == {
        "changed_modules": ()
    }


def test_service_builds_durable_task_request_before_conversation() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository(_context(owner_id, position_id, context_id))
    generated_id = uuid4()
    service = PositionIntelligenceService(repository, uuid_factory=lambda: generated_id)
    request_id = uuid4()

    result = service.create_task_request(
        owner_id=owner_id, position_id=position_id, request_id=request_id,
        canonical_payload_sha256="a" * 64, task_kind="jd",
        expected_context_version_id=context_id,
        material_attachment_ids=(), candidate_id=None,
        position_candidate_id=None,
    )

    assert result.task_request_id == generated_id
    assert result.client_request_id == request_id
    assert repository.calls[-1] == ("task_request", result)


def test_default_service_ids_are_deterministic_per_operation_and_request() -> None:
    owner_id, position_id, context_id = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository(_context(owner_id, position_id, context_id))
    service = PositionIntelligenceService(repository)
    request_id = uuid4()

    for _ in range(2):
        service.create_draft(
            owner_id=owner_id, position_id=position_id, request_id=request_id,
            base_context_version_id=None, official_version_id=None,
            modules={"mission": {}}, summary="summary",
        )
        service.create_task_request(
            owner_id=owner_id, position_id=position_id, request_id=request_id,
            canonical_payload_sha256="a" * 64, task_kind="jd",
            expected_context_version_id=None,
        )

    draft_ids = [
        value.context_version_id for kind, value in repository.calls
        if kind == "draft"
    ]
    task_ids = [
        value.task_request_id for kind, value in repository.calls
        if kind == "task_request"
    ]
    assert draft_ids[0] == draft_ids[1]
    assert task_ids[0] == task_ids[1]
    assert draft_ids[0] != task_ids[0]
