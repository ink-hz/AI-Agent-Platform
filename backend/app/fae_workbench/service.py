from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.observability.models import SessionFilters

from .models import (
    FAE_AGENT_ID,
    FAE_SOURCE_KIND,
    FaeAttentionSection,
    FaeFreshness,
    FaeIssueSection,
    FaeOverview,
    FaeReportPreviewSection,
    FaeSectionState,
    FaeSummary,
    FaeSummarySection,
    FaeTrendSection,
)
from .repository import FaeWorkbenchRepository


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CLOSED_ISSUE_STATUSES = frozenset({"closed", "duplicate", "not_actionable", "wont_fix"})


def _fae_filters(filters: SessionFilters) -> SessionFilters:
    return filters.model_copy(
        update={"agent_id": FAE_AGENT_ID, "source_kind": FAE_SOURCE_KIND}
    )


def _default_period(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(_SHANGHAI)
    period_end = datetime.combine(local_now.date(), time.min, tzinfo=_SHANGHAI)
    return period_end - timedelta(days=7), period_end


class FaeWorkbenchService:
    def __init__(self, repository: FaeWorkbenchRepository, observability, review) -> None:
        self._repository = repository
        self._observability = observability
        self._review = review

    async def list_sessions(self, filters: SessionFilters, limit: int, offset: int):
        return await self._observability.list_sessions(_fae_filters(filters), limit, offset)

    async def get_session(self, session_key: str):
        value = await self._observability.get_session(session_key)
        if value is None:
            return None
        if value.agent_id != FAE_AGENT_ID or value.source_kind != FAE_SOURCE_KIND:
            return None
        return value

    async def overview(self, now: datetime) -> FaeOverview:
        period_start, period_end = _default_period(now)
        snapshot, review_overview = await asyncio.gather(
            asyncio.to_thread(self._repository.snapshot, period_start, period_end),
            self._review.overview(agent_id=FAE_AGENT_ID),
            return_exceptions=True,
        )

        if isinstance(review_overview, BaseException):
            issues = FaeIssueSection(
                state=FaeSectionState(
                    status="unavailable", error_code="issues_unavailable"
                )
            )
            open_issue_count = None
        else:
            statuses = dict(review_overview.get("statuses") or {})
            open_issue_count = sum(
                int(count)
                for status, count in statuses.items()
                if status not in _CLOSED_ISSUE_STATUSES
            )
            issues = FaeIssueSection(
                state=FaeSectionState(status="available"), statuses=statuses
            )

        if isinstance(snapshot, BaseException):
            state = FaeSectionState(
                status="unavailable", error_code="operational_summary_unavailable"
            )
            summary = FaeSummarySection(state=state)
            attention = FaeAttentionSection(state=state)
            trends = FaeTrendSection(state=state)
            freshness = FaeFreshness(status="unavailable", data_as_of=None)
        else:
            state = FaeSectionState(status="available", as_of=snapshot.data_as_of)
            summary = FaeSummarySection(
                state=state,
                data=FaeSummary(
                    session_count=snapshot.session_count,
                    active_subject_count=snapshot.active_subject_count,
                    negative_feedback_events=snapshot.negative_feedback_events,
                    negative_turn_count=snapshot.negative_turn_count,
                    abnormal_session_count=snapshot.abnormal_session_count,
                    open_issue_count=open_issue_count,
                    p50_duration_ms=snapshot.p50_duration_ms,
                    p95_duration_ms=snapshot.p95_duration_ms,
                ),
            )
            attention = FaeAttentionSection(state=state, items=snapshot.attention)
            trends = FaeTrendSection(state=state, points=snapshot.trend)
            freshness = self._freshness(snapshot.data_as_of, now)

        return FaeOverview(
            period_start=period_start,
            period_end=period_end,
            freshness=freshness,
            summary=summary,
            attention=attention,
            trends=trends,
            issues=issues,
            reports=FaeReportPreviewSection(
                state=FaeSectionState(
                    status="unavailable", error_code="reports_not_integrated"
                )
            ),
        )

    @staticmethod
    def _freshness(data_as_of: datetime | None, now: datetime) -> FaeFreshness:
        if data_as_of is None:
            return FaeFreshness(status="unavailable", data_as_of=None)
        if now - data_as_of > timedelta(hours=36):
            return FaeFreshness(status="stale", data_as_of=data_as_of)
        return FaeFreshness(status="fresh", data_as_of=data_as_of)
