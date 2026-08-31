from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.fae_workbench.models import (
    FAE_AGENT_ID,
    FAE_SOURCE_KIND,
    FaeOverview,
    FaeOperationalSnapshot,
    FaeSessionAttention,
    FaeTrendPoint,
)
from app.fae_workbench.service import FaeWorkbenchService
from app.observability.models import Page, SessionDetail, SessionFilters, SessionSummary


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
LOCAL_PERIOD_END = datetime(2026, 9, 7, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
LOCAL_PERIOD_START = LOCAL_PERIOD_END - timedelta(days=7)


def fae_snapshot(*, data_as_of: datetime | None = NOW) -> FaeOperationalSnapshot:
    return FaeOperationalSnapshot(
        period_start=LOCAL_PERIOD_START,
        period_end=LOCAL_PERIOD_END,
        data_as_of=data_as_of,
        session_count=12,
        active_subject_count=7,
        negative_feedback_events=3,
        negative_turn_count=2,
        abnormal_session_count=1,
        p50_duration_ms=820,
        p95_duration_ms=3100,
        trend=[FaeTrendPoint(day=date(2026, 9, 6), sessions=12, negative_turns=2)],
        attention=[FaeSessionAttention(
            session_key="fae:session-1",
            title="设备掉线",
            last_active_at=NOW,
            reason="fallback",
        )],
    )


def admin_session() -> SessionDetail:
    return SessionDetail(
        session_key="admin:session-1",
        agent_id="ai-admin-agent",
        source_kind="admin",
        channel="admin",
        created_at=NOW,
        last_active_at=NOW,
        turn_count=1,
        feedback_count=0,
        review_count=0,
        freshness="fresh",
    )


class StaticRepository:
    def __init__(self, snapshot: FaeOperationalSnapshot | Exception | None = None) -> None:
        self.value = snapshot or fae_snapshot()
        self.period: tuple[datetime, datetime] | None = None

    def snapshot(self, period_start: datetime, period_end: datetime) -> FaeOperationalSnapshot:
        self.period = (period_start, period_end)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    def fae_turn_exists(self, _turn_key: str) -> bool:
        return True


class RecordingObservability:
    def __init__(self, session: SessionDetail | None = None) -> None:
        self.filters: SessionFilters | None = None
        self.session = session

    async def list_sessions(self, filters: SessionFilters, limit: int, offset: int):
        self.filters = filters
        return Page[SessionSummary](items=[], total=0, limit=limit, offset=offset)

    async def get_session(self, _session_key: str):
        return self.session


class StaticReview:
    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.agent_id: str | None = None
        self.statuses = statuses or {
            "pending_triage": 2,
            "closed": 1,
            "duplicate": 1,
            "not_actionable": 1,
            "wont_fix": 1,
        }

    async def overview(self, *, agent_id: str | None = None) -> dict:
        self.agent_id = agent_id
        return {"statuses": self.statuses}


class UnavailableReview:
    async def overview(self, *, agent_id: str | None = None) -> dict:
        raise RuntimeError("review unavailable")


def service_for(
    *,
    repository: StaticRepository | None = None,
    observability: RecordingObservability | None = None,
    review: StaticReview | UnavailableReview | None = None,
) -> FaeWorkbenchService:
    return FaeWorkbenchService(
        repository or StaticRepository(),
        observability or RecordingObservability(),
        review or StaticReview(),
    )


@pytest.mark.asyncio
async def test_list_sessions_overrides_browser_agent_and_source():
    observability = RecordingObservability()
    service = service_for(observability=observability)
    supplied = SessionFilters(
        agent_id="ai-admin-agent", source_kind="admin", query="Gemini"
    )

    await service.list_sessions(supplied, limit=50, offset=0)

    assert observability.filters is not None
    assert observability.filters.agent_id == FAE_AGENT_ID
    assert observability.filters.source_kind == FAE_SOURCE_KIND
    assert observability.filters.query == "Gemini"


@pytest.mark.asyncio
async def test_non_fae_session_is_hidden_as_missing():
    service = service_for(observability=RecordingObservability(admin_session()))

    assert await service.get_session("admin:session-1") is None


@pytest.mark.asyncio
async def test_overview_composes_available_operational_and_review_sections():
    repository = StaticRepository()
    review = StaticReview()

    overview = await service_for(repository=repository, review=review).overview(NOW)

    assert repository.period == (LOCAL_PERIOD_START, LOCAL_PERIOD_END)
    assert review.agent_id == FAE_AGENT_ID
    assert overview.summary.state.status == "available"
    assert overview.summary.data.session_count == 12
    assert overview.summary.data.open_issue_count == 2
    assert overview.attention.items[0].session_key == "fae:session-1"
    assert overview.trends.points[0].negative_turns == 2
    assert overview.issues.statuses == {"pending_triage": 2, "closed": 1, "duplicate": 1, "not_actionable": 1, "wont_fix": 1}
    assert overview.freshness.status == "fresh"
    assert overview.reports.state.error_code == "reports_not_integrated"


@pytest.mark.asyncio
async def test_review_failure_does_not_remove_operational_summary():
    overview = await service_for(review=UnavailableReview()).overview(NOW)

    assert overview.summary.state.status == "available"
    assert overview.summary.data.open_issue_count is None
    assert overview.issues.state.status == "unavailable"
    assert overview.reports.state.error_code == "reports_not_integrated"


@pytest.mark.asyncio
async def test_operational_failure_preserves_available_issue_state():
    overview = await service_for(
        repository=StaticRepository(RuntimeError("aggregate unavailable"))
    ).overview(NOW)

    assert overview.summary.state.status == "unavailable"
    assert overview.issues.state.status == "available"
    assert overview.summary.data is None


@pytest.mark.asyncio
async def test_overview_marks_old_operational_data_stale():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=NOW - timedelta(hours=37)))
    ).overview(NOW)

    assert overview.freshness.status == "stale"
    assert overview.freshness.data_as_of == NOW - timedelta(hours=37)


@pytest.mark.asyncio
async def test_overview_marks_missing_operational_data_as_unavailable():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=None))
    ).overview(NOW)

    assert overview.freshness.status == "unavailable"
    assert overview.freshness.data_as_of is None


@pytest.mark.asyncio
async def test_overview_treats_exactly_36_hour_old_data_as_fresh():
    overview = await service_for(
        repository=StaticRepository(fae_snapshot(data_as_of=NOW - timedelta(hours=36)))
    ).overview(NOW)

    assert overview.freshness.status == "fresh"


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_period_start():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["period_start"] = overview.period_start.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_freshness_timestamp():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["freshness"]["data_as_of"] = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)


@pytest.mark.asyncio
async def test_overview_api_rejects_naive_attention_timestamp():
    overview = await service_for().overview(NOW)
    payload = overview.model_dump()
    payload["attention"]["items"][0]["last_active_at"] = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError):
        FaeOverview.model_validate(payload)
