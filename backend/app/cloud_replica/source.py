from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from .models import (
    OperationEventProjection,
    RawAttachment,
    RawSession,
    RawTraceAggregate,
    RawTurn,
    ReviewFeedbackTotalsProjection,
    ReviewInboxProjection,
    ReviewIssueProjection,
)


SESSION_SQL = """
with candidate_sessions as (
  select
    session_key, agent_id, source_kind, channel, title, user_identity,
    created_at, last_active_at, primary_sender_name, primary_sender_department,
    greatest(last_active_at, coalesce(source_synced_at, last_active_at))
      as replica_updated_at
  from platform_read.sessions
  where last_active_at >= %(retention_floor)s
)
select
  session_key, agent_id, source_kind, channel, title, user_identity,
  created_at, last_active_at, primary_sender_name, primary_sender_department,
  replica_updated_at
from candidate_sessions
where (replica_updated_at, session_key) > (%(after)s, %(after_key)s)
  and replica_updated_at <= %(through)s
order by replica_updated_at, session_key
limit %(limit)s
""".strip()

TURN_SQL = """
select
    turn_key, session_key, turn_index, question, answer, created_at,
    question_at, answer_at, question_time_status, answer_time_status,
    outcome, fallback_used, duration_ms, trace_key
from platform_read.turns
where session_key = any(%(session_keys)s)
  and created_at <= %(through)s
order by session_key, turn_index, turn_key
""".strip()

ATTACHMENT_SQL = """
select
    a.attachment_id, t.turn_key as turn_key, a.direction, a.display_name,
    a.mime_type, a.size_bytes, a.received_or_generated_at, a.archive_status,
    a.delivery_status, a.expires_at
from platform_read.attachments a
join platform_read.turns t on a.turn_key = t.native_id
where t.turn_key = any(%(turn_keys)s)
  and a.received_or_generated_at <= %(through)s
order by t.turn_key, a.received_or_generated_at, a.attachment_id
""".strip()

TRACE_SQL = """
select
    trace_key, turn_key, status, duration_ms, engine, backend, model,
    input_tokens, output_tokens, cost_usd, error_class
from platform_read.traces
where turn_key = any(%(turn_keys)s)
  and started_at <= %(through)s
order by turn_key, trace_key
""".strip()

TRACE_STEP_SQL = """
select trace_key, kind, status, seq
from platform_read.trace_steps
where trace_key = any(%(trace_keys)s)
  and started_at <= %(through)s
order by trace_key, seq, step_key
""".strip()

REVIEW_ISSUE_SQL = """
select issue.id,issue.agent_id,issue.disposition as status,issue.priority,
  issue.title,issue.failure_layer,issue.owner,issue.updated_at,
  count(link.id) filter (where link.active) as linked_turn_count
from platform_review.feedback_issues issue
left join platform_review.feedback_issue_links link on link.issue_id=issue.id
where issue.updated_at <= %(through)s
group by issue.id
order by issue.updated_at,issue.id
""".strip()

REVIEW_INBOX_SQL = """
select feedback.agent_id,feedback.turn_key,count(*) as feedback_count,
  min(feedback.created_at) as first_feedback_at
from platform_read.feedback feedback
where feedback.sentiment='negative' and feedback.created_at <= %(through)s
  and not exists (
    select 1 from platform_review.feedback_issue_links link
    where link.agent_id=feedback.agent_id
      and link.source_turn_key=feedback.turn_key and link.active
  )
group by feedback.agent_id,feedback.turn_key
order by min(feedback.created_at),feedback.agent_id,feedback.turn_key
""".strip()


REVIEW_FEEDBACK_TOTALS_SQL = """
select agent_id,
  count(*) as feedback_rows,
  count(*) filter (where sentiment='negative') as negative_rows,
  count(distinct turn_key) filter (where sentiment='negative') as negative_turns,
  count(*) filter (where sentiment='positive') as positive_rows
from platform_read.feedback
where created_at <= %(through)s
group by agent_id
order by agent_id
""".strip()


class ReplicaSource:
    def __init__(
        self,
        database_url: str,
        *,
        connection_factory: Callable[..., Any] = psycopg.connect,
        operations_repository=None,
    ):
        self._database_url = database_url
        self._connection_factory = connection_factory
        self._operations_repository = operations_repository

    def fetch_management_projections(
        self, *, through: datetime
    ) -> tuple[object, ...]:
        if through.tzinfo is None:
            raise ValueError("invalid replica source window")
        options = "-c default_transaction_read_only=on -c statement_timeout=10000"
        with self._connection_factory(
            self._database_url, options=options, row_factory=dict_row
        ) as connection, connection.transaction():
            cursor = connection.cursor()
            cursor.execute("set transaction isolation level repeatable read, read only")
            issues = list(cursor.execute(
                REVIEW_ISSUE_SQL, {"through": through}
            ).fetchall())
            inbox = list(cursor.execute(
                REVIEW_INBOX_SQL, {"through": through}
            ).fetchall())
            totals = list(cursor.execute(
                REVIEW_FEEDBACK_TOTALS_SQL, {"through": through}
            ).fetchall())
        values: list[object] = [
            ReviewIssueProjection(
                issue_id=row["id"], agent_id=row["agent_id"],
                status=row["status"], priority=row["priority"],
                title=row["title"], failure_layer=row["failure_layer"],
                owner_display=row["owner"],
                linked_turn_count=int(row["linked_turn_count"]),
                updated_at=row["updated_at"],
            )
            for row in issues
        ]
        values.extend(
            ReviewInboxProjection(
                agent_id=row["agent_id"], turn_key=row["turn_key"],
                feedback_count=int(row["feedback_count"]),
                first_feedback_at=row["first_feedback_at"],
            )
            for row in inbox
        )
        values.extend(
            ReviewFeedbackTotalsProjection(
                agent_id=row["agent_id"],
                feedback_rows=int(row["feedback_rows"]),
                negative_rows=int(row["negative_rows"]),
                negative_turns=int(row["negative_turns"]),
                positive_rows=int(row["positive_rows"]),
                observed_at=through,
            )
            for row in totals
        )
        if self._operations_repository is not None:
            from app.operations.models import EventFilters

            page = self._operations_repository.list_events(
                EventFilters(date_to=through), 10_000, 0
            )
            values.extend(
                OperationEventProjection(
                    event_id=event.event_id,
                    agent_id=event.agent_id,
                    event_type=event.event_type,
                    severity=event.severity,
                    summary=event.summary,
                    occurred_at=event.occurred_at,
                )
                for event in page.items
                if event.agent_id is not None
            )
        return tuple(values)

    @staticmethod
    def _fetch(cursor, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cursor.execute(sql, params)
        return list(cursor.fetchall())

    def fetch_sessions(
        self,
        *,
        after: datetime,
        after_key: str,
        through: datetime,
        limit: int,
    ) -> tuple[RawSession, ...]:
        if (
            after.tzinfo is None
            or through.tzinfo is None
            or after >= through
            or not 1 <= limit <= 10_000
        ):
            raise ValueError("invalid replica source window")
        options = "-c default_transaction_read_only=on -c statement_timeout=10000"
        with self._connection_factory(
            self._database_url,
            options=options,
            row_factory=dict_row,
        ) as connection:
            with connection.transaction():
                cursor = connection.cursor()
                cursor.execute(
                    "set transaction isolation level repeatable read, read only"
                )
                common = {"through": through}
                session_rows = self._fetch(
                    cursor,
                    SESSION_SQL,
                    {
                        **common,
                        "after": after,
                        "after_key": after_key,
                        "retention_floor": through - timedelta(days=365),
                        "limit": limit,
                    },
                )
                session_keys = [row["session_key"] for row in session_rows]
                if not session_keys:
                    return ()
                turn_rows = self._fetch(
                    cursor,
                    TURN_SQL,
                    {**common, "session_keys": session_keys},
                )
                turn_keys = [row["turn_key"] for row in turn_rows]
                attachment_rows = (
                    self._fetch(
                        cursor,
                        ATTACHMENT_SQL,
                        {**common, "turn_keys": turn_keys},
                    )
                    if turn_keys
                    else []
                )
                trace_rows = (
                    self._fetch(
                        cursor,
                        TRACE_SQL,
                        {**common, "turn_keys": turn_keys},
                    )
                    if turn_keys
                    else []
                )
                trace_keys = [row["trace_key"] for row in trace_rows]
                step_rows = (
                    self._fetch(
                        cursor,
                        TRACE_STEP_SQL,
                        {**common, "trace_keys": trace_keys},
                    )
                    if trace_keys
                    else []
                )
        return self._assemble(
            session_rows, turn_rows, attachment_rows, trace_rows, step_rows
        )

    @staticmethod
    def _assemble(
        session_rows,
        turn_rows,
        attachment_rows,
        trace_rows,
        step_rows,
    ) -> tuple[RawSession, ...]:
        attachments_by_turn: dict[str, list[RawAttachment]] = {}
        for row in attachment_rows:
            attachments_by_turn.setdefault(row["turn_key"], []).append(
                RawAttachment(
                    attachment_id=row["attachment_id"],
                    direction=row["direction"],
                    display_name=row["display_name"],
                    mime_type=row["mime_type"],
                    size_bytes=row["size_bytes"],
                    received_or_generated_at=row["received_or_generated_at"],
                    archive_status=row["archive_status"],
                    delivery_status=row["delivery_status"],
                    expires_at=row["expires_at"],
                )
            )
        tools_by_trace: dict[str, list[str]] = {}
        for row in step_rows:
            tools_by_trace.setdefault(row["trace_key"], []).append(row["kind"])
        traces_by_turn = {
            row["turn_key"]: RawTraceAggregate(
                status=row["status"],
                duration_ms=row["duration_ms"],
                engine=row["engine"],
                backend=row["backend"],
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cost_usd=float(row["cost_usd"])
                if row["cost_usd"] is not None
                else None,
                error_class=row["error_class"],
                tool_categories=tuple(tools_by_trace.get(row["trace_key"], ())),
            )
            for row in trace_rows
        }
        turns_by_session: dict[str, list[RawTurn]] = {}
        for row in turn_rows:
            turns_by_session.setdefault(row["session_key"], []).append(
                RawTurn(
                    turn_key=row["turn_key"],
                    turn_index=row["turn_index"],
                    question=row["question"],
                    answer=row["answer"],
                    created_at=row["created_at"],
                    question_at=row["question_at"],
                    answer_at=row["answer_at"],
                    question_time_status=row["question_time_status"],
                    answer_time_status=row["answer_time_status"],
                    outcome=row["outcome"],
                    fallback_used=row["fallback_used"],
                    duration_ms=row["duration_ms"],
                    trace=traces_by_turn.get(row["turn_key"]),
                    attachments=tuple(attachments_by_turn.get(row["turn_key"], ())),
                )
            )
        return tuple(
            RawSession(
                session_key=row["session_key"],
                agent_id=row["agent_id"],
                source_kind=row["source_kind"],
                channel=row["channel"],
                title=row["title"],
                user_identity=row["user_identity"],
                primary_sender_name=row["primary_sender_name"],
                primary_sender_department=row["primary_sender_department"],
                created_at=row["created_at"],
                last_active_at=row["last_active_at"],
                replica_updated_at=row["replica_updated_at"],
                turns=tuple(turns_by_session.get(row["session_key"], ())),
            )
            for row in session_rows
        )
