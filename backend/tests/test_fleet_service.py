from datetime import date, datetime, timedelta, timezone

import pytest

from app.cluster.models import InstanceStatus, SourceStatus
from app.cluster.monitor import build_snapshot
from app.fleet.cache import CachedUsage
from app.fleet.catalog import AgentCatalog
from app.fleet.models import DataSourceStatus
from app.fleet.repository import DailyUsage, UsageRecord, UsageSnapshot
from app.fleet.service import FleetReadService


NOW = datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)
CURRENT_BOT_IDS = [
    "feishu-default",
    "hr-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "fae-bot",
    "test-bot",
    "marketing-gtm-bot",
    "marketing-intelligence-bot",
]


class StaticMonitor:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class StaticCache:
    def __init__(self, records=(), trend=(), healthy=True):
        snapshot = (
            UsageSnapshot(
                records=tuple(records),
                trend=tuple(trend),
                checked_at=NOW,
            )
            if healthy
            else None
        )
        self._value = CachedUsage(
            snapshot=snapshot,
            source=DataSourceStatus(
                healthy=healthy,
                checked_at=NOW.isoformat() if healthy else None,
                error=None if healthy else "usage_unavailable",
            ),
        )

    async def get(self):
        return self._value


def make_service(
    *records,
    trend=(),
    healthy=True,
    status_overrides=None,
    bot_ids=None,
):
    status_overrides = status_overrides or {}
    bot_ids = bot_ids or CURRENT_BOT_IDS
    statuses = [
        InstanceStatus(
            id=bot_id,
            name=bot_id,
            pm2_name=f"metabot-{bot_id}",
            port=9100 + index,
            status=status_overrides.get(bot_id, "healthy"),
            uptime_seconds=3600,
            checked_at=NOW.isoformat(),
        )
        for index, bot_id in enumerate(bot_ids)
    ]
    monitor = StaticMonitor(
        build_snapshot(
            statuses,
            SourceStatus(healthy=True, checked_at=NOW.isoformat()),
        )
    )
    return FleetReadService(
        monitor,
        AgentCatalog.default(),
        StaticCache(records, trend=trend, healthy=healthy),
        active_window_minutes=15,
    )


def get_agent(overview, bot_id):
    return next(item for item in overview.agents if item.id == bot_id)


@pytest.mark.asyncio
async def test_overview_merges_legacy_usage_into_current_agent():
    recent = NOW - timedelta(days=2)
    latest = NOW - timedelta(minutes=1)
    overview = await make_service(
        UsageRecord("marketing-bot", 14, 4, 2, recent, "legacy question"),
        UsageRecord(
            "marketing-prospecting-bot",
            44,
            8,
            3,
            latest,
            "current question",
        ),
    ).overview(now=NOW)

    card = get_agent(overview, "marketing-prospecting-bot")
    assert card.total_conversations == 58
    assert card.conversations_last_7d == 12
    assert card.last_activity_at == latest.isoformat()
    assert card.recent_summary == "current question"
    assert overview.summary.total_conversations == 58


@pytest.mark.asyncio
async def test_recent_healthy_agent_is_active_and_idle_agent_is_online():
    overview = await make_service(
        UsageRecord("hr-bot", 14, 4, 2, NOW - timedelta(minutes=2), "recent"),
        UsageRecord("fae-bot", 4, 0, 1, NOW - timedelta(days=2), "old"),
    ).overview(now=NOW)

    assert get_agent(overview, "hr-bot").state == "active"
    assert get_agent(overview, "fae-bot").state == "online"
    assert overview.summary.active_agents == 1
    assert overview.summary.running_agents == 9


@pytest.mark.asyncio
async def test_offline_runtime_state_wins_over_recent_usage():
    overview = await make_service(
        UsageRecord("hr-bot", 14, 4, 2, NOW - timedelta(minutes=1), "recent"),
        status_overrides={"hr-bot": "offline"},
    ).overview(now=NOW)

    assert get_agent(overview, "hr-bot").state == "offline"
    assert overview.summary.offline_agents == 1
    assert overview.summary.running_agents == 8


@pytest.mark.asyncio
async def test_missing_usage_is_unknown_not_zero():
    overview = await make_service(healthy=False).overview(now=NOW)

    assert overview.summary.total_conversations is None
    assert overview.summary.conversations_last_7d is None
    assert overview.trend == []
    assert all(agent.total_conversations is None for agent in overview.agents)
    assert overview.summary.running_agents == 9
    assert overview.usage_source.error == "usage_unavailable"


@pytest.mark.asyncio
async def test_healthy_empty_usage_is_zero_for_summary_and_every_agent():
    overview = await make_service().overview(now=NOW)

    assert overview.summary.total_conversations == 0
    assert overview.summary.conversations_last_7d == 0
    assert all(agent.total_conversations == 0 for agent in overview.agents)
    assert all(agent.conversations_last_7d == 0 for agent in overview.agents)


@pytest.mark.asyncio
async def test_unknown_current_runtime_bot_keeps_its_real_usage():
    overview = await make_service(
        UsageRecord("new-runtime-bot", 7, 3, 1, NOW, "new work"),
        bot_ids=["new-runtime-bot"],
    ).overview(now=NOW)

    assert overview.agents[0].id == "new-runtime-bot"
    assert overview.agents[0].total_conversations == 7
    assert overview.summary.total_conversations == 7


@pytest.mark.asyncio
async def test_healthy_usage_fills_seven_calendar_days_and_ignores_unresolved_bots():
    overview = await make_service(
        UsageRecord("pc-bot", 1000, 1000, 0, NOW, "ignored"),
        UsageRecord("hr-bot", 4, 4, 0, NOW, "real"),
        trend=[
            DailyUsage("hr-bot", date(2026, 7, 17), 1),
            DailyUsage("hr-bot", date(2026, 7, 21), 3),
            DailyUsage("pc-bot", date(2026, 7, 21), 999),
        ],
    ).overview(now=NOW)

    assert [point.date for point in overview.trend] == [
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
        "2026-07-19",
        "2026-07-20",
        "2026-07-21",
    ]
    assert [point.conversations for point in overview.trend] == [0, 0, 1, 0, 0, 0, 3]
    assert overview.summary.total_conversations == 4
    assert overview.summary.change_percent is None
