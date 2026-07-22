from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.observability.models import Page

from .models import EventFilters, NewOperationalEvent, OperationalEvent, RuleState, RunHealth


MIGRATION_VERSION_1 = """
create table if not exists operations_schema_version (
  version integer primary key,
  applied_at text not null
);
create table if not exists operational_events (
  event_id text primary key,
  agent_id text,
  agent_visibility text not null check (agent_visibility in ('business','system')),
  event_type text not null,
  event_family text not null,
  severity text not null check (severity in ('info','attention','critical')),
  status text not null check (status in ('active','resolved','historical')),
  title text not null,
  summary text not null,
  source_kind text not null,
  occurred_at text not null,
  first_observed_at text not null,
  last_observed_at text not null,
  resolved_at text,
  facts_json text not null,
  target_kind text,
  target_id text,
  target_path text,
  fingerprint text not null
);
create unique index if not exists uq_operational_active_fingerprint
  on operational_events(fingerprint) where status='active';
create index if not exists ix_operational_events_time
  on operational_events(occurred_at desc, event_id);
create index if not exists ix_operational_events_agent_time
  on operational_events(agent_id, occurred_at desc);
create table if not exists operational_rule_state (
  rule_key text primary key,
  value_json text not null,
  updated_at text not null
);
create table if not exists operational_runs (
  run_id text primary key,
  run_name text not null,
  status text not null,
  started_at text not null,
  finished_at text,
  cursor_json text not null,
  error_summary text
);
create index if not exists ix_operational_runs_name_time
  on operational_runs(run_name, started_at desc);
"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _event(row: sqlite3.Row) -> OperationalEvent:
    return OperationalEvent(
        event_id=row["event_id"],
        agent_id=row["agent_id"],
        agent_visibility=row["agent_visibility"],
        event_type=row["event_type"],
        event_family=row["event_family"],
        severity=row["severity"],
        status=row["status"],
        title=row["title"],
        summary=row["summary"],
        source_kind=row["source_kind"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        first_observed_at=datetime.fromisoformat(row["first_observed_at"]),
        last_observed_at=datetime.fromisoformat(row["last_observed_at"]),
        resolved_at=(
            datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
        ),
        facts=json.loads(row["facts_json"]),
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        target_path=row["target_path"],
        fingerprint=row["fingerprint"],
    )


def _event_values(
    event: NewOperationalEvent,
    *,
    event_id: str,
    status: str,
) -> tuple:
    occurred_at = _timestamp(event.occurred_at)
    return (
        event_id,
        event.agent_id,
        event.agent_visibility,
        event.event_type,
        event.event_family,
        event.severity,
        status,
        event.title,
        event.summary,
        event.source_kind,
        occurred_at,
        occurred_at,
        occurred_at,
        None,
        _json(event.facts),
        event.target_kind,
        event.target_id,
        event.target_path,
        event.fingerprint,
    )


INSERT_EVENT = """
insert into operational_events (
  event_id, agent_id, agent_visibility, event_type, event_family, severity,
  status, title, summary, source_kind, occurred_at, first_observed_at,
  last_observed_at, resolved_at, facts_json, target_kind, target_id,
  target_path, fingerprint
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class OperationsRepository:
    def __init__(self, path: str) -> None:
        self._path = path

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma journal_mode=WAL")
        connection.execute("pragma foreign_keys=ON")
        return connection

    def migrate(self) -> None:
        with self._connection() as connection:
            connection.executescript(MIGRATION_VERSION_1)
            connection.execute(
                "insert or ignore into operations_schema_version(version, applied_at) values (?, ?)",
                (1, _timestamp(datetime.now(timezone.utc))),
            )

    def schema_version(self) -> int:
        with self._connection() as connection:
            table = connection.execute(
                """
                select 1 from sqlite_master
                where type='table' and name='operations_schema_version'
                """
            ).fetchone()
            if table is None:
                return 0
            row = connection.execute(
                "select max(version) as version from operations_schema_version"
            ).fetchone()
        return int(row["version"] or 0)

    def upsert_active(self, event: NewOperationalEvent) -> OperationalEvent:
        connection = self._connection()
        try:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from operational_events where fingerprint=? and status='active'",
                (event.fingerprint,),
            ).fetchone()
            if row is None:
                event_id = str(uuid4())
                connection.execute(
                    INSERT_EVENT,
                    _event_values(event, event_id=event_id, status="active"),
                )
            else:
                event_id = row["event_id"]
                connection.execute(
                    """
                    update operational_events
                    set agent_id=?, agent_visibility=?, event_type=?, event_family=?,
                        severity=?, title=?, summary=?, source_kind=?, facts_json=?,
                        target_kind=?, target_id=?, target_path=?, last_observed_at=?
                    where event_id=?
                    """,
                    (
                        event.agent_id,
                        event.agent_visibility,
                        event.event_type,
                        event.event_family,
                        event.severity,
                        event.title,
                        event.summary,
                        event.source_kind,
                        _json(event.facts),
                        event.target_kind,
                        event.target_id,
                        event.target_path,
                        _timestamp(event.occurred_at),
                        event_id,
                    ),
                )
            result = connection.execute(
                "select * from operational_events where event_id=?", (event_id,)
            ).fetchone()
            connection.commit()
            return _event(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_historical(self, event: NewOperationalEvent) -> OperationalEvent:
        occurred_at = _timestamp(event.occurred_at)
        connection = self._connection()
        try:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from operational_events where fingerprint=? and occurred_at=?",
                (event.fingerprint, occurred_at),
            ).fetchone()
            if row is None:
                event_id = str(uuid4())
                connection.execute(
                    INSERT_EVENT,
                    _event_values(event, event_id=event_id, status="historical"),
                )
                row = connection.execute(
                    "select * from operational_events where event_id=?", (event_id,)
                ).fetchone()
            connection.commit()
            return _event(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def resolve_active(
        self,
        *,
        fingerprint: str,
        resolved_at: datetime,
        recovery_title: str,
        recovery_summary: str,
        recovery_facts: dict,
    ) -> OperationalEvent | None:
        connection = self._connection()
        try:
            connection.execute("begin immediate")
            active = connection.execute(
                "select * from operational_events where fingerprint=? and status='active'",
                (fingerprint,),
            ).fetchone()
            if active is None:
                connection.commit()
                return None

            recovery_type = self._recovery_type(active["event_type"], fingerprint)
            resolved_timestamp = _timestamp(resolved_at)
            connection.execute(
                """
                update operational_events
                set status='resolved', resolved_at=?, last_observed_at=?
                where event_id=?
                """,
                (resolved_timestamp, resolved_timestamp, active["event_id"]),
            )
            recovery = NewOperationalEvent(
                agent_id=active["agent_id"],
                agent_visibility=active["agent_visibility"],
                event_type=recovery_type,
                event_family="recovery",
                severity="info",
                title=recovery_title,
                summary=recovery_summary,
                source_kind=active["source_kind"],
                occurred_at=resolved_at,
                facts=recovery_facts,
                target_kind=active["target_kind"],
                target_id=active["target_id"],
                target_path=active["target_path"],
                fingerprint=f"recovery:{fingerprint}:{resolved_timestamp}",
            )
            recovery_id = str(uuid4())
            connection.execute(
                INSERT_EVENT,
                _event_values(recovery, event_id=recovery_id, status="historical"),
            )
            row = connection.execute(
                "select * from operational_events where event_id=?", (recovery_id,)
            ).fetchone()
            connection.commit()
            return _event(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _recovery_type(event_type: str, fingerprint: str) -> str:
        if event_type == "runtime_offline" or fingerprint.startswith("runtime:"):
            return "runtime_recovered"
        if event_type == "remote_sync_unavailable" or fingerprint.startswith("sync:"):
            return "sync_recovered"
        if event_type == "business_data_unavailable" or fingerprint.startswith("data:"):
            return "data_access_recovered"
        raise ValueError(f"unsupported recoverable event type: {event_type}")

    def expire_active_occurrences(self, event_family: str, before: datetime) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                update operational_events set status='historical'
                where event_family=? and status='active' and occurred_at < ?
                """,
                (event_family, _timestamp(before)),
            )
            return cursor.rowcount

    def get_rule_state(self, rule_key: str) -> RuleState | None:
        with self._connection() as connection:
            row = connection.execute(
                "select * from operational_rule_state where rule_key=?", (rule_key,)
            ).fetchone()
        if row is None:
            return None
        return RuleState(
            rule_key=row["rule_key"],
            value=json.loads(row["value_json"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def put_rule_state(self, state: RuleState) -> RuleState:
        with self._connection() as connection:
            connection.execute(
                """
                insert into operational_rule_state(rule_key, value_json, updated_at)
                values (?, ?, ?)
                on conflict(rule_key) do update set
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at
                """,
                (state.rule_key, _json(state.value), _timestamp(state.updated_at)),
            )
        return state

    def record_run(self, run: RunHealth) -> RunHealth:
        with self._connection() as connection:
            connection.execute(
                """
                insert into operational_runs(
                  run_id, run_name, status, started_at, finished_at,
                  cursor_json, error_summary
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    run.run_name,
                    run.status,
                    _timestamp(run.started_at),
                    _timestamp(run.finished_at) if run.finished_at else None,
                    _json(run.cursor),
                    run.error_summary,
                ),
            )
        return run

    def latest_run(self, run_name: str) -> RunHealth | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                select * from operational_runs where run_name=?
                order by started_at desc, rowid desc limit 1
                """,
                (run_name,),
            ).fetchone()
        if row is None:
            return None
        return RunHealth(
            run_name=row["run_name"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            cursor=json.loads(row["cursor_json"]),
            error_summary=row["error_summary"],
        )

    def list_events(
        self,
        filters: EventFilters,
        limit: int,
        offset: int,
    ) -> Page[OperationalEvent]:
        conditions: list[str] = []
        params: list[object] = []
        for column, value in (
            ("agent_id", filters.agent_id),
            ("event_type", filters.event_type),
            ("severity", filters.severity),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                params.append(value)
        if filters.date_from is not None:
            conditions.append("occurred_at>=?")
            params.append(_timestamp(filters.date_from))
        if filters.date_to is not None:
            conditions.append("occurred_at<=?")
            params.append(_timestamp(filters.date_to))
        where = f"where {' and '.join(conditions)}" if conditions else ""
        with self._connection() as connection:
            total = int(
                connection.execute(
                    f"select count(*) as count from operational_events {where}", params
                ).fetchone()["count"]
            )
            rows = connection.execute(
                f"""
                select * from operational_events {where}
                order by occurred_at desc, event_id
                limit ? offset ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return Page[OperationalEvent](
            items=[_event(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_active_attention(self, agent_visibility: str) -> tuple[OperationalEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                select * from operational_events
                where status='active' and agent_visibility=?
                  and severity in ('attention', 'critical')
                order by
                  case severity when 'critical' then 0 else 1 end,
                  occurred_at desc,
                  event_id
                """,
                (agent_visibility,),
            ).fetchall()
        return tuple(_event(row) for row in rows)
