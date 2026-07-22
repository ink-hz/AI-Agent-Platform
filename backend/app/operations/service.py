from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from app.observability.models import Page

from .models import (
    BriefFreshness,
    EventFilters,
    OperationsBrief,
    OperationalEvent,
    UsageBrief,
    UsageLeader,
)
from .repository import OperationsRepository


DEFAULT_INTERVALS: dict[str, float] = {
    "runtime": 10.0,
    "sync": 60.0,
    "data_access": 60.0,
    "usage": 300.0,
    "execution": 300.0,
    "lifecycle": 600.0,
}
_MAX_SQLITE_LIMIT = 2_147_483_647
_USAGE_TITLE_SUFFIX = " received new conversations"


class OperationsService:
    def __init__(
        self,
        repository: OperationsRepository,
        *,
        intervals: Mapping[str, float] | None = None,
    ) -> None:
        self._repository = repository
        self._intervals = dict(DEFAULT_INTERVALS if intervals is None else intervals)

    def brief(self, now: datetime | None = None) -> OperationsBrief:
        period_end = self._aware(now or datetime.now(timezone.utc))
        period_start = period_end - timedelta(hours=24)
        filters = EventFilters(date_from=period_start, date_to=period_end)
        events = self.list_events(filters, _MAX_SQLITE_LIMIT, 0).items
        attention = list(self._repository.list_active_attention("business"))
        freshness = self._freshness(period_end)
        return OperationsBrief(
            period_start=period_start,
            period_end=period_end,
            freshness=freshness,
            can_claim_healthy=(
                freshness.status == "current" and not attention
            ),
            attention=attention,
            usage=self._usage(events),
            changes=events[:5],
        )

    def list_events(
        self,
        filters: EventFilters,
        limit: int,
        offset: int,
    ) -> Page[OperationalEvent]:
        if filters.agent_id is not None:
            return self._repository.list_events(filters, limit, offset)

        all_events = self._repository.list_events(
            filters,
            _MAX_SQLITE_LIMIT,
            0,
        ).items
        business_events = [
            item for item in all_events if item.agent_visibility == "business"
        ]
        return Page[OperationalEvent](
            items=business_events[offset : offset + limit],
            total=len(business_events),
            limit=limit,
            offset=offset,
        )

    def _freshness(self, now: datetime) -> BriefFreshness:
        runs = {
            group: self._repository.latest_run(group)
            for group in self._intervals
        }
        failed_groups = [
            group
            for group, run in runs.items()
            if run is not None and run.status == "failed"
        ]
        successful = {
            group: run
            for group, run in runs.items()
            if run is not None and run.status == "succeeded"
        }
        evaluated_at = (
            min(self._run_time(run) for run in successful.values())
            if successful
            else None
        )
        if not successful:
            status = "unavailable"
        elif len(successful) != len(runs):
            status = "partial"
        elif any(
            now - self._run_time(successful[group])
            > timedelta(seconds=interval * 2)
            for group, interval in self._intervals.items()
        ):
            status = "stale"
        else:
            status = "current"
        return BriefFreshness(
            status=status,
            evaluated_at=evaluated_at,
            failed_groups=failed_groups,
        )

    @classmethod
    def _run_time(cls, run) -> datetime:
        return cls._aware(run.finished_at or run.started_at)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @classmethod
    def _usage(cls, events: list[OperationalEvent]) -> UsageBrief:
        totals: dict[str, int] = {}
        names: dict[str, str] = {}
        for item in events:
            if item.event_type != "new_conversations" or item.agent_id is None:
                continue
            count = item.facts.get("conversations")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                continue
            totals[item.agent_id] = totals.get(item.agent_id, 0) + count
            names[item.agent_id] = cls._agent_name(item)
        leaders = [
            UsageLeader(
                agent_id=agent_id,
                agent_name=names[agent_id],
                conversations=conversations,
            )
            for agent_id, conversations in totals.items()
            if conversations > 0
        ]
        leaders.sort(
            key=lambda item: (-item.conversations, item.agent_name, item.agent_id)
        )
        return UsageBrief(
            conversations=sum(totals.values()),
            active_agents=len(leaders),
            leaders=leaders,
        )

    @staticmethod
    def _agent_name(event: OperationalEvent) -> str:
        name = event.facts.get("agent_name")
        if isinstance(name, str) and name:
            return name
        if event.title.endswith(_USAGE_TITLE_SUFFIX):
            return event.title[: -len(_USAGE_TITLE_SUFFIX)]
        return event.agent_id or "Unknown Agent"
