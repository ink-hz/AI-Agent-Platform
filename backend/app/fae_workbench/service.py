from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.observability.models import SessionFilters
from app.review.http_models import CreateIssue, LinkTurn
from app.review.repository import ReviewNotFound

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

    async def _fae_issue(self, issue_id: UUID) -> dict:
        detail = await self._review.issue_detail(issue_id)
        try:
            agent_id = detail["issue"]["agent_id"]
        except (KeyError, TypeError):
            raise ReviewNotFound("issue not found") from None
        if agent_id != FAE_AGENT_ID:
            raise ReviewNotFound("issue not found")
        links = detail.get("links") or ()
        if not isinstance(links, (list, tuple)) or any(
            not isinstance(link, dict) or link.get("agent_id") != FAE_AGENT_ID
            for link in links
        ):
            raise ReviewNotFound("issue not found")
        return detail

    async def _fae_turn_exists(self, turn_key: str) -> bool:
        return await asyncio.to_thread(self._repository.fae_turn_exists, turn_key)

    async def issue_overview(self):
        return await self._review.overview(agent_id=FAE_AGENT_ID)

    async def issue_inbox(self, *, limit: int, offset: int):
        return await self._review.inbox(
            agent_id=FAE_AGENT_ID,
            limit=limit,
            offset=offset,
        )

    async def list_issues(self, *, limit: int, offset: int):
        return await self._review.list_issues(
            agent_id=FAE_AGENT_ID,
            limit=limit,
            offset=offset,
        )

    async def issue_detail(self, issue_id: UUID):
        return await self._fae_issue(issue_id)

    async def turn_summaries(self, turn_keys: list[str]):
        scoped_keys = []
        for turn_key in dict.fromkeys(turn_keys):
            if await self._fae_turn_exists(turn_key):
                scoped_keys.append(turn_key)
        if not scoped_keys:
            return []
        summaries = await self._review.turn_summaries(turn_keys=scoped_keys)
        for summary in summaries:
            issue_id = summary.get("issue_id")
            if issue_id is not None:
                try:
                    scoped_issue_id = UUID(str(issue_id))
                except (TypeError, ValueError):
                    raise ReviewNotFound("issue not found") from None
                await self._fae_issue(scoped_issue_id)
        return summaries

    async def create_issue(self, payload, *, actor: str):
        data = payload.model_dump()
        data["agent_id"] = FAE_AGENT_ID
        scoped = CreateIssue.model_validate(data)
        if scoped.origin_turn_key is not None and not await self._fae_turn_exists(
            scoped.origin_turn_key
        ):
            raise ReviewNotFound("turn not found")
        return await self._review.create_issue(scoped, actor=actor)

    async def update_issue(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.update_issue(issue_id, payload, actor=actor)

    async def link_turn(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        if not await self._fae_turn_exists(payload.source_turn_key):
            raise ReviewNotFound("turn not found")
        data = payload.model_dump()
        data["agent_id"] = FAE_AGENT_ID
        scoped = LinkTurn.model_validate(data)
        return await self._review.link_turn(issue_id, scoped, actor=actor)

    async def move_link(
        self,
        issue_id: UUID,
        link_id: UUID,
        payload,
        *,
        actor: str,
    ):
        await self._fae_issue(issue_id)
        await self._fae_issue(payload.target_issue_id)
        return await self._review.move_link(
            issue_id,
            link_id,
            payload,
            actor=actor,
        )

    async def merge_issue(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        await self._fae_issue(payload.target_issue_id)
        return await self._review.merge_issue(issue_id, payload, actor=actor)

    async def mark_fix_ready(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.mark_fix_ready(issue_id, payload, actor=actor)

    async def add_evidence(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.add_evidence(issue_id, payload, actor=actor)

    async def verify_evidence(self, evidence_id: UUID, payload, *, actor: str):
        issue_id = await self._review.evidence_issue_id(evidence_id)
        await self._fae_issue(issue_id)
        return await self._review.verify_evidence(evidence_id, payload, actor=actor)

    async def start_replay(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        return await self._review.start_replay(issue_id, payload, actor=actor)

    async def semantic_review(self, replay_id: UUID, payload, *, actor: str):
        issue_id = await self._review.replay_issue_id(replay_id)
        await self._fae_issue(issue_id)
        return await self._review.semantic_review(replay_id, payload, actor=actor)

    async def set_disposition(self, issue_id: UUID, payload, *, actor: str):
        await self._fae_issue(issue_id)
        if payload.canonical_issue_id is not None:
            await self._fae_issue(payload.canonical_issue_id)
        return await self._review.set_disposition(issue_id, payload, actor=actor)

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
