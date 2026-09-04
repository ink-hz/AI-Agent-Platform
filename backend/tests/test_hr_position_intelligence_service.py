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
