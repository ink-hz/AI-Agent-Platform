from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

import psycopg
from psycopg.rows import dict_row


USAGE_SQL = """
with answer_turns as (
  select c.bot_id, m.turn_id, min(m.occurred_at) as answered_at
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'assistant'
  group by c.bot_id, m.turn_id
), latest_user as (
  select distinct on (c.bot_id) c.bot_id, m.content
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'user'
  order by c.bot_id, m.occurred_at desc
)
select a.bot_id,
  count(distinct a.turn_id)::bigint as total_conversations,
  count(distinct a.turn_id) filter (
    where a.answered_at >= now() - interval '7 days')::bigint
    as conversations_last_7d,
  count(distinct a.turn_id) filter (
    where a.answered_at >= now() - interval '14 days'
      and a.answered_at < now() - interval '7 days')::bigint
    as conversations_previous_7d,
  max(a.answered_at) as last_activity_at,
  left(max(u.content), 120) as recent_summary
from answer_turns a
left join latest_user u using (bot_id)
group by a.bot_id
order by a.bot_id
"""


TREND_SQL = """
with answer_turns as (
  select c.bot_id, m.turn_id, min(m.occurred_at) as answered_at
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'assistant'
  group by c.bot_id, m.turn_id
)
select (answered_at at time zone 'Asia/Shanghai')::date as date,
  count(distinct (bot_id, turn_id))::bigint as conversations
from answer_turns
where (answered_at at time zone 'Asia/Shanghai')::date
  >= (now() at time zone 'Asia/Shanghai')::date - 6
group by date
order by date
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


@dataclass(frozen=True)
class DailyUsage:
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
        except Exception as error:
            raise FlywheelReadError("flywheel query failed") from error

        return UsageSnapshot(
            records=tuple(
                UsageRecord(
                    bot_id=row["bot_id"],
                    total_conversations=int(row["total_conversations"]),
                    conversations_last_7d=int(row["conversations_last_7d"]),
                    conversations_previous_7d=int(
                        row["conversations_previous_7d"]
                    ),
                    last_activity_at=row["last_activity_at"],
                    recent_summary=row["recent_summary"],
                )
                for row in usage_rows
            ),
            trend=tuple(
                DailyUsage(
                    date=row["date"],
                    conversations=int(row["conversations"]),
                )
                for row in trend_rows
            ),
            checked_at=self._now(),
        )


class UnavailableFlywheelRepository:
    def fetch_usage(self) -> UsageSnapshot:
        raise FlywheelReadError("flywheel query failed")
