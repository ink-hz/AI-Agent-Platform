# ruff: noqa: C408
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.hr.panorama_models import PanoramaRun, TalentSource
from app.hr.panorama_service import PanoramaService

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.source: TalentSource | None = None
        self.run_value: PanoramaRun | None = None

    def create_source(self, command):
        self.calls.append(("create_source", command))
        self.source = TalentSource(
            command.source_id,
            command.owner_id,
            command.client_request_id,
            "company",
            command.company_key,
            command.canonical_name,
            command.aliases,
            command.approved_urls,
            command.active,
            NOW,
            NOW,
        )
        return self.source

    def list_sources(self, *args, **kwargs):
        self.calls.append(("list_sources", args, kwargs))
        return () if self.source is None else (self.source,)

    def create_run(self, command):
        self.calls.append(("create_run", command))
        self.run_value = PanoramaRun(
            command.run_id,
            command.owner_id,
            command.client_request_id,
            command.selected_source_ids,
            command.conversation_id,
            "queued",
            None,
            {},
            1,
            None,
            None,
            NOW,
            NOW,
        )
        return self.run_value

    def run(self, *args):
        self.calls.append(("run", args))
        return self.run_value

    def report(self, *args):
        self.calls.append(("report", args))
        return "report"

    def list_insights(self, *args, **kwargs):
        self.calls.append(("list_insights", args, kwargs))
        return ("report-summary",)

    def relevant_insights(self, *args, **kwargs):
        self.calls.append(("relevant", args, kwargs))
        return ("insight",)


class RecordingCoordinator:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []
        self.preflight_calls: list[tuple] = []

    def preflight(self, owner_id, source_ids):
        self.preflight_calls.append((owner_id, source_ids))

    def submit(self, run_id: UUID) -> UUID:
        self.run_ids.append(run_id)
        return run_id


def test_add_company_replays_with_a_stable_company_kind_and_identity() -> None:
    owner_id, request_id = uuid4(), uuid4()
    repository = RecordingRepository()
    service = PanoramaService(repository)
    values = dict(
        owner_id=owner_id,
        request_id=request_id,
        canonical_name="联合光电",
        aliases=("Union Optech",),
        approved_urls=("https://www.union-optech.com/jobs",),
    )

    first = service.add_company(**values)
    second = service.add_company(**values)

    commands = [call[1] for call in repository.calls]
    assert first.source_kind == "company"
    assert second.source_id == first.source_id
    assert commands[0].source_id == commands[1].source_id
    assert commands[0].company_key == commands[1].company_key
    assert commands[0].company_key.startswith("company-")


def test_service_delegates_owner_scoped_reads_run_and_report() -> None:
    owner_id, request_id, conversation_id = uuid4(), uuid4(), uuid4()
    source_ids = (uuid4(), uuid4())
    repository = RecordingRepository()
    service = PanoramaService(repository, coordinator=RecordingCoordinator())

    assert service.list_companies(owner_id, include_inactive=True, limit=9) == ()
    run = service.start_run(
        owner_id=owner_id,
        request_id=request_id,
        source_ids=source_ids,
        conversation_id=conversation_id,
    )
    assert service.run_status(owner_id, run.run_id) is run
    assert service.report(owner_id, uuid4()) == "report"
    assert repository.calls[0] == (
        "list_sources",
        (owner_id,),
        {"include_inactive": True, "limit": 9},
    )
    run_command = repository.calls[1][1]
    assert run_command.selected_source_ids == source_ids
    assert run_command.conversation_id == conversation_id


def test_start_run_replays_the_same_durable_run_and_resubmits_same_turn_identity() -> (
    None
):
    owner_id, request_id, conversation_id = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository()
    coordinator = RecordingCoordinator()
    service = PanoramaService(repository, coordinator=coordinator)
    values = {
        "owner_id": owner_id,
        "request_id": request_id,
        "source_ids": (uuid4(),),
        "conversation_id": conversation_id,
    }

    first = service.start_run(**values)
    replay = service.start_run(**values)

    assert replay.run_id == first.run_id
    assert coordinator.run_ids == [first.run_id, first.run_id]
    assert coordinator.preflight_calls == [
        (owner_id, values["source_ids"]),
        (owner_id, values["source_ids"]),
    ]


def test_start_run_without_runtime_fails_before_creating_a_run() -> None:
    from app.hr.panorama_repository import PanoramaUnavailable

    repository = RecordingRepository()
    service = PanoramaService(repository)

    with pytest.raises(PanoramaUnavailable, match="runtime unavailable"):
        service.start_run(
            owner_id=uuid4(),
            request_id=uuid4(),
            source_ids=(uuid4(),),
            conversation_id=uuid4(),
        )

    assert repository.calls == []


def test_preflight_failure_happens_before_run_insert() -> None:
    class FailingCoordinator(RecordingCoordinator):
        def preflight(self, owner_id, source_ids):
            raise ValueError("destination invalid")

    repository = RecordingRepository()
    service = PanoramaService(repository, coordinator=FailingCoordinator())

    with pytest.raises(ValueError, match="destination invalid"):
        service.start_run(
            owner_id=uuid4(),
            request_id=uuid4(),
            source_ids=(uuid4(),),
            conversation_id=uuid4(),
        )

    assert repository.calls == []


def test_service_lists_owner_scoped_reports_with_a_strict_repository_limit() -> None:
    owner_id = uuid4()
    repository = RecordingRepository()
    service = PanoramaService(repository)

    assert service.list_reports(owner_id, limit=17) == ("report-summary",)
    assert repository.calls == [
        ("list_insights", (owner_id,), {"limit": 17}),
    ]


def test_relevant_insights_keeps_position_and_limit_in_repository_boundary() -> None:
    owner_id, position_id = uuid4(), uuid4()
    repository = RecordingRepository()
    service = PanoramaService(repository)

    assert service.relevant_insights(
        owner_id, "参考联合光电修订 JR", position_id, limit=3
    ) == ("insight",)
    assert repository.calls[-1] == (
        "relevant",
        (owner_id, "参考联合光电修订 JR", position_id),
        {"limit": 3},
    )


def test_service_rejects_invalid_repository_and_uuid_factory() -> None:
    with pytest.raises(ValueError, match="repository invalid"):
        PanoramaService(object())
    with pytest.raises(ValueError, match="UUID factory invalid"):
        PanoramaService(RecordingRepository(), uuid_factory="bad")  # type: ignore[arg-type]


def test_custom_uuid_factory_is_used_only_as_an_explicit_test_seam() -> None:
    generated = UUID("00000000-0000-0000-0000-000000000123")
    repository = RecordingRepository()
    service = PanoramaService(repository, uuid_factory=lambda: generated)

    source = service.add_company(
        owner_id=uuid4(),
        request_id=uuid4(),
        canonical_name="Company",
        aliases=(),
        approved_urls=("https://example.com/jobs",),
    )

    assert source.source_id == generated
