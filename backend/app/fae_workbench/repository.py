from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from .models import (
    FAE_AGENT_ID,
    FAE_SOURCE_KIND,
    FaeOperationalSnapshot,
    FaeSessionAttention,
    FaeTrendPoint,
)


_RESOLVED_OUTCOMES = "'resolved', 'completed', 'succeeded'"

SUMMARY_SQL = f"""
with scoped_sessions as (
  select s.session_key, s.native_id, s.source_synced_at
  from platform_read.sessions s
  where s.agent_id = '{FAE_AGENT_ID}'
    and s.source_kind = '{FAE_SOURCE_KIND}'
    and s.last_active_at >= %s and s.last_active_at < %s
), scoped_turns as (
  select t.session_key, t.turn_key, t.answer, t.fallback_used, t.outcome,
    t.duration_ms
  from platform_read.turns t
  join scoped_sessions s on s.session_key = t.session_key
  where t.agent_id = '{FAE_AGENT_ID}' and t.source_kind = '{FAE_SOURCE_KIND}'
), negative_feedback as (
  select f.feedback_key, f.turn_key
  from platform_read.feedback f
  join scoped_turns t on t.turn_key = f.turn_key
  where f.agent_id = '{FAE_AGENT_ID}' and f.source_kind = '{FAE_SOURCE_KIND}'
    and f.sentiment = 'negative'
), abnormal_sessions as (
  select s.session_key,
    coalesce(bool_or(
      t.turn_key is not null and (
        nullif(btrim(t.answer), '') is null
        or t.fallback_used is true
        or (t.outcome is not null and lower(t.outcome) not in ({_RESOLVED_OUTCOMES}))
      )
    ), false) as abnormal
  from scoped_sessions s
  left join scoped_turns t on t.session_key = s.session_key
  group by s.session_key
), subjects as (
  select count(distinct coalesce(source.external_user_id, source.user_id))
    filter (where coalesce(source.external_user_id, source.user_id) is not null)
    ::bigint as active_subject_count
  from platform_source_fae.chat_sessions source
  join scoped_sessions s on s.native_id = source.id::text
)
select
  (select count(*) from scoped_sessions)::bigint as session_count,
  (select active_subject_count from subjects) as active_subject_count,
  (select count(*) from negative_feedback)::bigint as negative_feedback_events,
  (select count(distinct turn_key) from negative_feedback)::bigint as negative_turn_count,
  (select count(*) from abnormal_sessions where abnormal)::bigint as abnormal_session_count,
  (select round(percentile_cont(0.5) within group (order by duration_ms))::bigint
     from scoped_turns where duration_ms is not null and duration_ms >= 0) as p50_duration_ms,
  (select round(percentile_cont(0.95) within group (order by duration_ms))::bigint
     from scoped_turns where duration_ms is not null and duration_ms >= 0) as p95_duration_ms,
  (select max(source_synced_at) from scoped_sessions) as data_as_of
""".strip()

TREND_SQL = f"""
with scoped_sessions as (
  select s.session_key, s.last_active_at
  from platform_read.sessions s
  where s.agent_id = '{FAE_AGENT_ID}'
    and s.source_kind = '{FAE_SOURCE_KIND}'
    and s.last_active_at >= %s and s.last_active_at < %s
), negative_feedback as (
  select distinct t.session_key, f.turn_key
  from platform_read.turns t
  join scoped_sessions s on s.session_key = t.session_key
  join platform_read.feedback f on f.turn_key = t.turn_key
  where t.agent_id = '{FAE_AGENT_ID}' and t.source_kind = '{FAE_SOURCE_KIND}'
    and f.agent_id = '{FAE_AGENT_ID}' and f.source_kind = '{FAE_SOURCE_KIND}'
    and f.sentiment = 'negative'
)
select (s.last_active_at at time zone 'Asia/Shanghai')::date as day,
  count(*)::bigint as sessions,
  count(distinct negative_feedback.turn_key)::bigint as negative_turns
from scoped_sessions s
left join negative_feedback on negative_feedback.session_key = s.session_key
group by day
order by day
""".strip()

ATTENTION_SQL = f"""
with scoped_sessions as (
  select s.session_key, s.title, s.last_active_at
  from platform_read.sessions s
  where s.agent_id = '{FAE_AGENT_ID}'
    and s.source_kind = '{FAE_SOURCE_KIND}'
    and s.last_active_at >= %s and s.last_active_at < %s
), scoped_turns as (
  select t.session_key, t.answer, t.fallback_used, t.outcome
  from platform_read.turns t
  join scoped_sessions s on s.session_key = t.session_key
  where t.agent_id = '{FAE_AGENT_ID}' and t.source_kind = '{FAE_SOURCE_KIND}'
)
select s.session_key, s.title, s.last_active_at,
  case
    when bool_or(t.fallback_used is true) then 'fallback'
    when bool_or(t.outcome is not null and lower(t.outcome) not in ({_RESOLVED_OUTCOMES}))
      then 'failed_outcome'
    when bool_or(nullif(btrim(t.answer), '') is null) then 'empty_answer'
  end as reason
from scoped_sessions s
join scoped_turns t on t.session_key = s.session_key
group by s.session_key, s.title, s.last_active_at
having bool_or(t.fallback_used is true)
  or bool_or(t.outcome is not null and lower(t.outcome) not in ({_RESOLVED_OUTCOMES}))
  or bool_or(nullif(btrim(t.answer), '') is null)
order by s.last_active_at desc, s.session_key
limit %s
""".strip()


class FaeWorkbenchReadError(RuntimeError):
    pass


class FaeWorkbenchRepository(Protocol):
    def snapshot(
        self, period_start: datetime, period_end: datetime
    ) -> FaeOperationalSnapshot: ...

    def fae_turn_exists(self, turn_key: str) -> bool: ...


class PsycopgFaeWorkbenchRepository:
    def __init__(
        self, database_url: str, *, connect: Callable[..., Any] = psycopg.connect
    ) -> None:
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
            row_factory=dict_row,
        )

    def snapshot(
        self, period_start: datetime, period_end: datetime
    ) -> FaeOperationalSnapshot:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                summary = cursor.execute(SUMMARY_SQL, (period_start, period_end)).fetchone()
                trend = cursor.execute(TREND_SQL, (period_start, period_end)).fetchall()
                attention = cursor.execute(
                    ATTENTION_SQL, (period_start, period_end, 10)
                ).fetchall()
            return FaeOperationalSnapshot(
                period_start=period_start,
                period_end=period_end,
                trend=[FaeTrendPoint(**row) for row in trend],
                attention=[FaeSessionAttention(**row) for row in attention],
                **(summary or {}),
            )
        except Exception:
            raise FaeWorkbenchReadError("fae_workbench_query_failed") from None

    def fae_turn_exists(self, turn_key: str) -> bool:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    f"""select exists(select 1 from platform_read.turns
                    where turn_key=%s and agent_id='{FAE_AGENT_ID}'
                      and source_kind='{FAE_SOURCE_KIND}') as found""",
                    (turn_key,),
                ).fetchone()
            return bool(row and row["found"])
        except Exception:
            raise FaeWorkbenchReadError("fae_workbench_query_failed") from None


class ReplicaFaeWorkbenchRepository:
    """FAE aggregate facade over the sanitized cloud-replica read boundary."""

    def __init__(
        self,
        repository: Any,
        *,
        feedback_totals: Callable[[datetime, datetime], tuple[int, int] | None]
        | None = None,
    ) -> None:
        self._repository = repository
        self._feedback_totals = feedback_totals

    def snapshot(
        self, period_start: datetime, period_end: datetime
    ) -> FaeOperationalSnapshot:
        try:
            aggregate = self._repository.fae_operational_aggregate(
                period_start, period_end
            )
            feedback = (
                self._feedback_totals(period_start, period_end)
                if self._feedback_totals is not None
                else None
            )
            if feedback is None:
                raise ValueError
            negative_feedback_events, negative_turn_count = feedback
            return FaeOperationalSnapshot(
                period_start=period_start,
                period_end=period_end,
                negative_feedback_events=negative_feedback_events,
                negative_turn_count=negative_turn_count,
                **aggregate,
            )
        except Exception:
            raise FaeWorkbenchReadError("fae_workbench_query_failed") from None

    def fae_turn_exists(self, turn_key: str) -> bool:
        try:
            return bool(self._repository.fae_turn_exists(turn_key))
        except Exception:
            raise FaeWorkbenchReadError("fae_workbench_query_failed") from None
