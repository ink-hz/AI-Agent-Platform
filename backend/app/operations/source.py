from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.fleet.catalog import AgentCatalog, AgentProfile

from .models import ExecutionObservation, UsageOccurrence


LOCAL_USAGE_SQL = """
select t.turn_key, t.agent_id, t.source_kind, t.created_at
from platform_read.turns t
where t.source_kind='metabot'
  and t.created_at > %s and t.created_at <= %s
  and nullif(btrim(t.answer), '') is not null
order by t.created_at, t.turn_key
"""


REMOTE_USAGE_SQL = """
select t.turn_key, t.agent_id, t.source_kind, t.created_at
from platform_read.turns t
where t.source_kind=%s
  and nullif(btrim(t.answer), '') is not null
"""


def _execution_sql(filters: str) -> str:
    return f"""
select t.turn_key, t.session_key, t.agent_id, t.source_kind, t.created_at,
  'empty_answer' as signal_type
from platform_read.turns t
where {filters}
  and nullif(btrim(t.answer), '') is null
union all
select t.turn_key, t.session_key, t.agent_id, t.source_kind, t.created_at,
  'fallback' as signal_type
from platform_read.turns t
where {filters}
  and t.fallback_used is true
union all
select t.turn_key, t.session_key, t.agent_id, t.source_kind, t.created_at,
  'incomplete' as signal_type
from platform_read.turns t
where {filters}
  and lower(coalesce(t.outcome, '')) in ('failed', 'error', 'incomplete')
union all
select distinct t.turn_key, t.session_key, t.agent_id, t.source_kind, t.created_at,
  'tool_error' as signal_type
from platform_read.turns t
join platform_read.traces r on r.turn_key=t.turn_key
join platform_read.trace_steps s on s.trace_key=r.trace_key
where {filters}
  and r.detail_availability='available'
  and s.kind='tool_call'
  and (s.error_summary is not null or lower(coalesce(s.status, '')) in ('failed', 'error'))
order by created_at, turn_key, signal_type
"""


LOCAL_EXECUTION_SQL = _execution_sql(
    "t.source_kind='metabot' and t.created_at > %s and t.created_at <= %s"
)


_SUPPORTED_SIGNALS = {"tool_error", "fallback", "empty_answer", "incomplete"}


class PsycopgOperationsSource:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable = psycopg.connect,
        catalog: AgentCatalog | None = None,
    ) -> None:
        self._database_url = database_url
        self._connect = connect
        self._catalog = catalog or AgentCatalog.default()
        self._profiles = {
            profile.id: profile for profile in self._catalog.all_profiles()
        }

    def fetch_local_usage(
        self, after: datetime, through: datetime
    ) -> tuple[UsageOccurrence, ...]:
        return self._usage_rows(LOCAL_USAGE_SQL, (after, through))

    def fetch_remote_usage(
        self,
        source_kind: str,
        *,
        created_after: datetime | None = None,
        created_through: datetime | None = None,
    ) -> tuple[UsageOccurrence, ...]:
        self._validate_remote_source(source_kind)
        statement = REMOTE_USAGE_SQL
        params: list[object] = [source_kind]
        if created_after is not None:
            statement += "  and t.created_at > %s\n"
            params.append(created_after)
        if created_through is not None:
            statement += "  and t.created_at <= %s\n"
            params.append(created_through)
        statement += "order by t.created_at, t.turn_key\n"
        return self._usage_rows(statement, tuple(params))

    def fetch_usage(
        self, after: datetime, through: datetime
    ) -> tuple[UsageOccurrence, ...]:
        return self.fetch_local_usage(after, through)

    def _usage_rows(
        self, statement: str, params: tuple
    ) -> tuple[UsageOccurrence, ...]:
        rows = self._fetchall(statement, params)
        occurrences: list[UsageOccurrence] = []
        for row in rows:
            resolved = self._profile(row["agent_id"])
            if resolved is None:
                continue
            agent_id, _profile = resolved
            occurrences.append(
                UsageOccurrence(
                    turn_key=row["turn_key"],
                    agent_id=agent_id,
                    source_kind=row["source_kind"],
                    occurred_at=row["created_at"],
                )
            )
        return tuple(occurrences)

    def fetch_local_execution(
        self, after: datetime, through: datetime
    ) -> tuple[ExecutionObservation, ...]:
        params = (after, through) * 4
        return self._execution_rows(LOCAL_EXECUTION_SQL, params)

    def fetch_remote_execution(
        self,
        source_kind: str,
        *,
        created_after: datetime | None = None,
        created_through: datetime | None = None,
    ) -> tuple[ExecutionObservation, ...]:
        self._validate_remote_source(source_kind)
        filters = ["t.source_kind=%s"]
        values: list[object] = [source_kind]
        if created_after is not None:
            filters.append("t.created_at > %s")
            values.append(created_after)
        if created_through is not None:
            filters.append("t.created_at <= %s")
            values.append(created_through)
        branch_params = tuple(values)
        return self._execution_rows(
            _execution_sql(" and ".join(filters)),
            branch_params * 4,
        )

    def fetch_execution(
        self, after: datetime, through: datetime
    ) -> tuple[ExecutionObservation, ...]:
        return self.fetch_local_execution(after, through)

    def _execution_rows(
        self, statement: str, params: tuple
    ) -> tuple[ExecutionObservation, ...]:
        rows = self._fetchall(statement, params)
        observations: list[ExecutionObservation] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            signal_type = row["signal_type"]
            key = (row["turn_key"], signal_type)
            if signal_type not in _SUPPORTED_SIGNALS or key in seen:
                continue
            resolved = self._profile(row["agent_id"])
            if resolved is None:
                continue
            agent_id, profile = resolved
            seen.add(key)
            observations.append(
                ExecutionObservation(
                    turn_key=row["turn_key"],
                    session_key=row["session_key"],
                    agent_id=agent_id,
                    agent_name=profile.name,
                    agent_visibility=profile.visibility,
                    source_kind=row["source_kind"],
                    signal_type=signal_type,
                    occurred_at=row["created_at"],
                )
            )
        return tuple(observations)

    @staticmethod
    def _validate_remote_source(source_kind: str) -> None:
        if source_kind not in {"fae", "admin"}:
            raise ValueError("remote Operations source must be fae or admin")

    def _fetchall(self, statement: str, params: tuple) -> list[dict]:
        with self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                return list(cursor.execute(statement, params).fetchall())

    def _profile(self, source_agent_id: str) -> tuple[str, AgentProfile] | None:
        agent_id = self._catalog.canonical_id(source_agent_id)
        if agent_id is None:
            return None
        profile = self._profiles.get(agent_id)
        if profile is None:
            return None
        return agent_id, profile
