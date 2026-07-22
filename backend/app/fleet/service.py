from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.cluster.monitor import ClusterMonitor

from .cache import UsageCache
from .catalog import AgentCatalog
from .models import (
    DataSourceStatus,
    FleetAgent,
    FleetOverview,
    FleetState,
    FleetSummary,
    TrendPoint,
)
from .repository import UsageRecord


SHANGHAI = ZoneInfo("Asia/Shanghai")
RUNTIME_STALE_AFTER = timedelta(seconds=120)


@dataclass
class _UsageTotal:
    total: int = 0
    last_7d: int = 0
    previous_7d: int = 0
    last_activity_at: datetime | None = None
    recent_summary: str | None = None
    sessions: int = 0
    last_synced_at: datetime | None = None

    def add(self, record: UsageRecord) -> None:
        self.total += record.total_conversations
        self.last_7d += record.conversations_last_7d
        self.previous_7d += record.conversations_previous_7d
        self.sessions += record.session_count
        if record.last_synced_at is not None and (
            self.last_synced_at is None or record.last_synced_at > self.last_synced_at
        ):
            self.last_synced_at = record.last_synced_at
        if record.last_activity_at is not None and (
            self.last_activity_at is None
            or record.last_activity_at > self.last_activity_at
        ):
            self.last_activity_at = record.last_activity_at
            self.recent_summary = record.recent_summary


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class FleetReadService:
    def __init__(
        self,
        monitor: ClusterMonitor,
        catalog: AgentCatalog,
        usage_cache: UsageCache,
        *,
        active_window_minutes: int = 15,
        remote_monitor=None,
    ) -> None:
        self._monitor = monitor
        self._catalog = catalog
        self._usage_cache = usage_cache
        self._active_window = timedelta(minutes=active_window_minutes)
        self._remote_monitor = remote_monitor

    async def overview(self, now: datetime | None = None) -> FleetOverview:
        now = now or datetime.now(timezone.utc)
        cluster = self._monitor.snapshot()
        remote = self._remote_monitor.snapshot() if self._remote_monitor else None
        cached = await self._usage_cache.get()
        instances = list(cluster.instances) + (list(remote.agents) if remote else [])
        current_ids = {instance.id for instance in instances}

        usage_by_id: dict[str, _UsageTotal] | None = None
        if cached.snapshot is not None:
            usage_by_id = {}
            for record in cached.snapshot.records:
                canonical_id = self._catalog.canonical_id(record.bot_id)
                if canonical_id is None or canonical_id not in current_ids:
                    continue
                usage_by_id.setdefault(canonical_id, _UsageTotal()).add(record)

        agents = [
            self._build_agent(instance, usage_by_id, now)
            for instance in instances
        ]
        business_agents = [
            agent for agent in agents if agent.visibility == "business"
        ]
        business_ids = {agent.id for agent in business_agents}
        trend = (
            self._build_trend(cached.snapshot.trend, business_ids, now)
            if cached.snapshot is not None
            else []
        )
        state_rank = {
            "offline": 0,
            "degraded": 1,
            "checking": 2,
            "unknown": 2,
            "active": 3,
            "online": 3,
        }
        agents.sort(
            key=lambda item: (
                state_rank[item.state],
                -(item.total_conversations or 0),
                item.name,
            )
        )

        business_usage = [
            (usage_by_id or {}).get(agent_id, _UsageTotal())
            for agent_id in business_ids
        ]
        total = sum(item.total for item in business_usage)
        last_7d = sum(item.last_7d for item in business_usage)
        previous_7d = sum(
            item.previous_7d for item in business_usage
        )
        usage_available = usage_by_id is not None
        states = [agent.state for agent in business_agents]
        summary = FleetSummary(
            total_agents=len(business_agents),
            running_agents=sum(state in {"active", "online"} for state in states),
            active_agents=states.count("active"),
            degraded_agents=states.count("degraded"),
            offline_agents=states.count("offline"),
            checking_agents=states.count("checking"),
            total_conversations=total if usage_available else None,
            conversations_last_7d=last_7d if usage_available else None,
            conversations_previous_7d=previous_7d if usage_available else None,
            change_percent=(
                round((last_7d - previous_7d) / previous_7d * 100, 1)
                if usage_available and previous_7d > 0
                else None
            ),
        )

        remote_checked_at = remote.checked_at.isoformat() if remote and remote.checked_at else None
        runtime_checked_at = remote_checked_at or cluster.source.checked_at
        source_checked_at = _parse_time(runtime_checked_at)
        runtime_healthy = cluster.source.healthy and (remote.healthy if remote else True)
        return FleetOverview(
            summary=summary,
            trend=trend,
            agents=agents,
            runtime_source=DataSourceStatus(
                healthy=runtime_healthy,
                checked_at=runtime_checked_at,
                stale=(
                    source_checked_at is None
                    or now - source_checked_at > RUNTIME_STALE_AFTER
                ),
                error=cluster.source.error or (remote.error if remote else None),
            ),
            usage_source=cached.source,
        )

    def _build_agent(self, instance, usage_by_id, now: datetime) -> FleetAgent:
        profile = self._catalog.profile(instance.id, instance.name)
        usage = (
            usage_by_id.get(instance.id, _UsageTotal())
            if usage_by_id is not None
            else None
        )
        last_activity = usage.last_activity_at if usage else None
        state = self._state(instance.status, last_activity, now)
        return FleetAgent(
            id=instance.id,
            name=profile.name,
            domain=profile.domain,
            description=profile.description,
            glyph=profile.glyph,
            accent=profile.accent,
            visibility=profile.visibility,
            state=state,
            live_since=profile.live_since,
            live_since_basis=profile.live_since_basis,
            last_updated_at=profile.last_updated_at,
            last_updated_basis=profile.last_updated_basis,
            current_runtime_seconds=instance.uptime_seconds,
            total_conversations=usage.total if usage else None,
            conversations_last_7d=usage.last_7d if usage else None,
            last_activity_at=(last_activity.isoformat() if last_activity else None),
            recent_summary=usage.recent_summary if usage else None,
            session_count=usage.sessions if usage else None,
            last_synced_at=(
                usage.last_synced_at.isoformat()
                if usage and usage.last_synced_at else None
            ),
            data_freshness=self._data_freshness(instance.id, usage, now),
        )

    def _data_freshness(self, agent_id: str, usage, now: datetime) -> str:
        if usage is None:
            return "unavailable"
        if agent_id not in {"ai-fae-agent", "ai-admin-agent"}:
            return "live"
        if usage.last_synced_at is None or now - usage.last_synced_at > timedelta(hours=36):
            return "stale"
        return "fresh"

    def _state(
        self,
        runtime_state: str,
        last_activity: datetime | None,
        now: datetime,
    ) -> FleetState:
        if runtime_state in {"offline", "degraded", "checking", "unknown"}:
            return runtime_state
        if last_activity is not None and now - last_activity <= self._active_window:
            return "active"
        return "online"

    def _build_trend(self, raw_trend, current_ids: set[str], now: datetime):
        counts: dict[date, int] = {}
        for item in raw_trend:
            canonical_id = self._catalog.canonical_id(item.bot_id)
            if canonical_id is None or canonical_id not in current_ids:
                continue
            counts[item.date] = counts.get(item.date, 0) + item.conversations
        today = now.astimezone(SHANGHAI).date()
        return [
            TrendPoint(
                date=(today - timedelta(days=offset)).isoformat(),
                conversations=counts.get(today - timedelta(days=offset), 0),
            )
            for offset in range(6, -1, -1)
        ]
