from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

import psycopg
from psycopg.rows import dict_row


USAGE_SQL = """
with answer_turns as (
  select agent_id as bot_id, turn_key as turn_id, created_at as answered_at, question
  from platform_read.turns
  where answer <> ''
), latest_user as (
  select distinct on (bot_id) bot_id, question as content
  from answer_turns
  order by bot_id, answered_at desc
), session_totals as (
  select agent_id as bot_id, count(*)::bigint as session_count,
    max(source_synced_at) as last_synced_at
  from platform_read.sessions
  group by agent_id
)
select a.bot_id,
  count(distinct a.turn_id)::bigint as total_conversations,
  count(distinct a.turn_id) filter (
    where (a.answered_at at time zone 'Asia/Shanghai')::date
      >= (now() at time zone 'Asia/Shanghai')::date - 6)::bigint
    as conversations_last_7d,
  count(distinct a.turn_id) filter (
    where (a.answered_at at time zone 'Asia/Shanghai')::date
      >= (now() at time zone 'Asia/Shanghai')::date - 13
      and (a.answered_at at time zone 'Asia/Shanghai')::date
        < (now() at time zone 'Asia/Shanghai')::date - 6)::bigint
    as conversations_previous_7d,
  max(a.answered_at) as last_activity_at,
  left(max(u.content), 120) as recent_summary,
  coalesce(max(s.session_count), 0)::bigint as session_count,
  max(s.last_synced_at) as last_synced_at
from answer_turns a
left join latest_user u using (bot_id)
left join session_totals s using (bot_id)
group by a.bot_id
order by a.bot_id
"""


TREND_SQL = """
with answer_turns as (
  select agent_id as bot_id, turn_key as turn_id, created_at as answered_at
  from platform_read.turns
  where answer <> ''
)
select bot_id, (answered_at at time zone 'Asia/Shanghai')::date as date,
  count(distinct (bot_id, turn_id))::bigint as conversations
from answer_turns
where (answered_at at time zone 'Asia/Shanghai')::date
  >= (now() at time zone 'Asia/Shanghai')::date - 6
group by bot_id, date
order by date, bot_id
"""


class FlywheelReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageRecord:
    bot_id: str
    total_conversations: int
    conversations_last_7d: int
    conversations_previous_7d: int
    last_activity_at: datetime | None
    recent_summary: str | None
    session_count: int = 0
    last_synced_at: datetime | None = None


@dataclass(frozen=True)
class DailyUsage:
    bot_id: str
    date: date
    conversations: int


@dataclass(frozen=True)
class UsageSnapshot:
    records: tuple[UsageRecord, ...]
    trend: tuple[DailyUsage, ...]
    checked_at: datetime


class FlywheelRepository(Protocol):
    def fetch_usage(self) -> UsageSnapshot: ...


class PsycopgFlywheelRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable = psycopg.connect,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._database_url = database_url
        self._connect = connect
        self._now = now

    def fetch_usage(self) -> UsageSnapshot:
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=5000",
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    usage_rows = cursor.execute(USAGE_SQL).fetchall()
                    trend_rows = cursor.execute(TREND_SQL).fetchall()
            return UsageSnapshot(
                records=tuple(
                    UsageRecord(
                        bot_id=row["bot_id"],
                        total_conversations=int(row["total_conversations"]),
                        conversations_last_7d=int(
                            row["conversations_last_7d"]
                        ),
                        conversations_previous_7d=int(
                            row["conversations_previous_7d"]
                        ),
                        last_activity_at=row["last_activity_at"],
                        recent_summary=row["recent_summary"],
                        session_count=int(row.get("session_count") or 0),
                        last_synced_at=row.get("last_synced_at"),
                    )
                    for row in usage_rows
                ),
                trend=tuple(
                    DailyUsage(
                        bot_id=row["bot_id"],
                        date=row["date"],
                        conversations=int(row["conversations"]),
                    )
                    for row in trend_rows
                ),
                checked_at=self._now(),
            )
        except Exception as error:
            raise FlywheelReadError("flywheel query failed") from error


class UnavailableFlywheelRepository:
    def fetch_usage(self) -> UsageSnapshot:
        raise FlywheelReadError("flywheel query failed")
