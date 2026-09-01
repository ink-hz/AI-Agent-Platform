from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.review.progress_projection import progress_from_detail
from app.review.scope_sql import (
    CANONICAL_EVENT_PAIR_INVALID_SQL,
    HISTORICAL_LINK_EVENT_INVALID_SQL,
)

from .models import (
    FaeReportProjection,
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
    outcome, fallback_used, duration_ms, trace_key,
    coalesce((
      select array_agg(feedback.sentiment order by feedback.created_at,
                       feedback.feedback_key)
      from platform_read.feedback feedback
      where feedback.agent_id=turn.agent_id
        and feedback.turn_key=turn.turn_key
        and feedback.created_at <= %(through)s
    ), '{}') as feedback_sentiments,
    coalesce((
      select array_agg(review.status order by review.updated_at, review.review_key)
      from platform_read.reviews review
      where review.turn_key=turn.turn_key
        and review.updated_at <= %(through)s
    ), '{}') as review_statuses
from platform_read.turns turn
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

REVIEW_ISSUE_SQL = f"""
with recursive canonical_walk as (
  select issue.id as root_id,issue.id as current_id,
    issue.canonical_issue_id as next_id,array[issue.id] as path,false as cycle
  from platform_review.feedback_issues issue
  union all
  select walk.root_id,target.id,target.canonical_issue_id,
    walk.path || target.id,target.id=any(walk.path)
  from canonical_walk walk
  join platform_review.feedback_issues target on target.id=walk.next_id
  where walk.next_id is not null and not walk.cycle
)
select issue.id,issue.agent_id,issue.disposition as status,issue.priority,
  issue.title,issue.failure_layer,issue.owner,issue.created_at,issue.updated_at,
  issue.origin_turn_key,issue.secondary_layers,issue.root_cause,issue.impact_scope,
  issue.fix_ready_at,issue.disposition,issue.canonical_issue_id,
  issue.disposition_reason,issue.row_version,
  (select count(*) from platform_review.feedback_issue_links link
    where link.issue_id=issue.id and link.active) as linked_turn_count,
  coalesce((select array_agg(link.source_turn_key order by link.source_turn_key)
    from platform_review.feedback_issue_links link
    where link.issue_id=issue.id and link.active), '{{}}') as linked_turn_keys,
  not (
    (issue.origin_turn_key is not null and (
      not exists (select 1 from platform_read.turns origin
        where origin.turn_key=issue.origin_turn_key)
      or exists (select 1 from platform_read.turns origin
        where origin.turn_key=issue.origin_turn_key and (
          origin.agent_id is distinct from issue.agent_id
          or (issue.agent_id='ai-fae-agent'
              and origin.source_kind is distinct from 'fae')
        ))
    ))
    or (issue.canonical_issue_id is not null and (
      canonical.id is null
      or canonical.agent_id is distinct from issue.agent_id
    ))
    or exists (
      select 1 from platform_review.feedback_issue_links link
      left join platform_read.turns linked_turn
        on linked_turn.turn_key=link.source_turn_key
      where link.issue_id=issue.id and (
        link.agent_id is distinct from issue.agent_id
        or linked_turn.turn_key is null
        or linked_turn.agent_id is distinct from issue.agent_id
        or (issue.agent_id='ai-fae-agent'
            and linked_turn.source_kind is distinct from 'fae')
        or exists (
          select 1 from unnest(link.source_feedback_keys)
            as stored_feedback(feedback_key)
          left join platform_read.feedback linked_feedback
            on linked_feedback.feedback_key=stored_feedback.feedback_key
           and linked_feedback.agent_id=link.agent_id
           and linked_feedback.turn_key=link.source_turn_key
          where linked_feedback.feedback_key is null
        )
        or cardinality(link.source_feedback_keys) <> (
          select count(distinct feedback_key)
          from unnest(link.source_feedback_keys)
            as stored_feedback_key(feedback_key)
        )
      )
    )
    or exists (
      select 1 from platform_review.feedback_replay_runs replay
      left join platform_review.feedback_issue_links replay_link
        on replay_link.id=replay.issue_link_id
      where replay.issue_id=issue.id and (
        replay_link.id is null
        or replay_link.issue_id is distinct from issue.id
      )
    )
    or {HISTORICAL_LINK_EVENT_INVALID_SQL}
    or {CANONICAL_EVENT_PAIR_INVALID_SQL}
    or exists (select 1 from canonical_walk walk
      where walk.root_id=issue.id and walk.cycle)
  ) as scope_valid
from platform_review.feedback_issues issue
left join platform_review.feedback_issues canonical
  on canonical.id=issue.canonical_issue_id
where issue.updated_at <= %(through)s
order by issue.updated_at,issue.id
""".strip()


REVIEW_LINK_SQL = """
select detail_link.id,detail_link.issue_id,detail_link.agent_id,
  detail_link.source_turn_key,detail_link.source_feedback_keys,
  detail_link.link_role,detail_link.linked_by,detail_link.linked_at,
  detail_link.active,detail_link.link_reason,
  turn.question as source_question,turn.answer as source_answer,
  turn.turn_index as source_turn_index,turn.session_key as source_session_key,
  turn.created_at as source_created_at,turn.details as source_details,
  turn.sources as source_sources,turn.trace_key as source_trace_key,
  turn.outcome as source_outcome,turn.fallback_used as source_fallback_used,
  context.turns as source_context
from platform_review.feedback_issue_links detail_link
join platform_review.feedback_issues detail_issue on detail_issue.id=detail_link.issue_id
left join platform_read.turns turn
  on turn.agent_id=detail_link.agent_id
 and turn.turn_key=detail_link.source_turn_key
left join lateral (
  select jsonb_agg(
    jsonb_build_object(
      'turn_index',prior.turn_index,
      'question',prior.question,
      'answer',prior.answer
    ) order by prior.turn_index,prior.created_at
  ) as turns
  from platform_read.turns prior
  where prior.agent_id=turn.agent_id
    and prior.session_key=turn.session_key
    and prior.turn_index < turn.turn_index
    and prior.created_at <= %(through)s
) context on true
where detail_issue.updated_at <= %(through)s
order by detail_link.issue_id,detail_link.linked_at,detail_link.id
""".strip()


REVIEW_EVIDENCE_SQL = """
select detail_evidence.id,detail_evidence.issue_id,detail_evidence.evidence_type,
  detail_evidence.repository,detail_evidence.reference,detail_evidence.url,
  detail_evidence.version,detail_evidence.commit_sha,
  detail_evidence.release_manifest_ref,detail_evidence.environment,
  detail_evidence.observed_at,detail_evidence.observed_by,
  detail_evidence.verification_status,detail_evidence.verification_details
from platform_review.feedback_fix_evidence detail_evidence
join platform_review.feedback_issues detail_issue on detail_issue.id=detail_evidence.issue_id
where detail_issue.updated_at <= %(through)s
  and detail_evidence.observed_at <= %(through)s
order by detail_evidence.issue_id,detail_evidence.observed_at,detail_evidence.id
""".strip()


REVIEW_REPLAY_SQL = """
select detail_replay.id,detail_replay.issue_id,detail_replay.issue_link_id,
  detail_replay.attempt_no,detail_replay.expected_version,
  detail_replay.actual_version,detail_replay.expected_git_sha,
  detail_replay.actual_git_sha,detail_replay.configured_model,
  detail_replay.actual_model,detail_replay.answer,detail_replay.sources,
  detail_replay.done,detail_replay.trace_id,detail_replay.duration_ms,
  detail_replay.execution_status,detail_replay.runtime_gate,
  detail_replay.runtime_failure_reason,detail_replay.semantic_verdict,
  detail_replay.review_method,detail_replay.reviewer,
  detail_replay.review_reason,detail_replay.started_at,detail_replay.completed_at
from platform_review.feedback_replay_runs detail_replay
join platform_review.feedback_issues detail_issue on detail_issue.id=detail_replay.issue_id
where detail_issue.updated_at <= %(through)s
  and detail_replay.started_at <= %(through)s
order by detail_replay.issue_id,detail_replay.started_at,detail_replay.attempt_no
""".strip()


REVIEW_EVENT_SQL = """
select detail_event.id,detail_event.issue_id,detail_event.event_type,
  detail_event.actor,detail_event.reason,detail_event.before,detail_event.after,
  detail_event.created_at
from platform_review.feedback_issue_events detail_event
join platform_review.feedback_issues detail_issue on detail_issue.id=detail_event.issue_id
where detail_issue.updated_at <= %(through)s
  and detail_event.created_at <= %(through)s
order by detail_event.issue_id,detail_event.created_at,detail_event.id
""".strip()

REVIEW_INBOX_SQL = """
select feedback.agent_id,feedback.turn_key,count(*) as feedback_count,
  min(feedback.created_at) as first_feedback_at,true as scope_valid
from platform_read.feedback feedback
where feedback.sentiment='negative' and feedback.created_at <= %(through)s
  and not exists (
    select 1 from platform_review.feedback_issue_links link
    join platform_review.feedback_issues linked_issue
      on linked_issue.id=link.issue_id
    where link.agent_id=feedback.agent_id
      and linked_issue.agent_id=feedback.agent_id
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


FAE_REPORT_SQL = """
select report_id,report_version,generated_at,payload
from platform_fae_reports.reports
where generated_at <= %(through)s
order by generated_at,report_id,report_version
""".strip()


FAE_REPORT_LINK_SQL = """
select report.report_id,report.report_version,link.finding_id,link.issue_id
from platform_fae_reports.finding_issue_links link
join platform_fae_reports.reports report on report.report_pk=link.report_pk
where link.unlinked_at is null and link.linked_at <= %(through)s
order by report.report_id,report.report_version,link.finding_id,link.issue_id
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
        options = "-c default_transaction_read_only=on -c statement_timeout=30000"
        with self._connection_factory(
            self._database_url, options=options, row_factory=dict_row
        ) as connection, connection.transaction():
            cursor = connection.cursor()
            cursor.execute("set transaction isolation level repeatable read, read only")
            issues = list(cursor.execute(
                REVIEW_ISSUE_SQL, {"through": through}
            ).fetchall())
            issue_links = list(cursor.execute(
                REVIEW_LINK_SQL, {"through": through}
            ).fetchall())
            issue_evidence = list(cursor.execute(
                REVIEW_EVIDENCE_SQL, {"through": through}
            ).fetchall())
            issue_replays = list(cursor.execute(
                REVIEW_REPLAY_SQL, {"through": through}
            ).fetchall())
            issue_events = list(cursor.execute(
                REVIEW_EVENT_SQL, {"through": through}
            ).fetchall())
            inbox = list(cursor.execute(
                REVIEW_INBOX_SQL, {"through": through}
            ).fetchall())
            totals = list(cursor.execute(
                REVIEW_FEEDBACK_TOTALS_SQL, {"through": through}
            ).fetchall())
            reports = list(cursor.execute(
                FAE_REPORT_SQL, {"through": through}
            ).fetchall())
            report_links = list(cursor.execute(
                FAE_REPORT_LINK_SQL, {"through": through}
            ).fetchall())
        def group(rows: list[dict], field: str = "issue_id") -> dict[object, list[dict]]:
            grouped: dict[object, list[dict]] = {}
            for value in rows:
                grouped.setdefault(value[field], []).append(dict(value))
            return grouped

        links_by_issue = group(issue_links)
        evidence_by_issue = group(issue_evidence)
        replays_by_issue = group(issue_replays)
        events_by_issue = group(issue_events)
        values: list[object] = []
        for row in issues:
            issue_id = row["id"]
            detail = {
                "issue": dict(row),
                "links": links_by_issue.get(issue_id, []),
                "evidence": evidence_by_issue.get(issue_id, []),
                "replays": replays_by_issue.get(issue_id, []),
                "events": events_by_issue.get(issue_id, []),
            }
            progress = asdict(progress_from_detail(detail))
            values.append(ReviewIssueProjection(
                issue_id=issue_id, agent_id=row["agent_id"],
                status=row["status"], priority=row["priority"],
                title=row["title"], failure_layer=row["failure_layer"],
                owner_display=row["owner"],
                linked_turn_count=int(row["linked_turn_count"]),
                linked_turn_keys=tuple(row.get("linked_turn_keys") or ()),
                created_at=row["created_at"], updated_at=row["updated_at"],
                scope_valid=row["scope_valid"] is True,
                detail_schema_version=1,
                origin_turn_key=row.get("origin_turn_key"),
                root_cause=row.get("root_cause") or "",
                impact_scope=row.get("impact_scope") or "",
                secondary_layers=tuple(row.get("secondary_layers") or ()),
                links=tuple(detail["links"]),
                evidence=tuple(detail["evidence"]),
                replays=tuple(detail["replays"]),
                events=tuple(detail["events"]),
                progress=progress,
            ))
        values.extend(
            ReviewInboxProjection(
                agent_id=row["agent_id"], turn_key=row["turn_key"],
                feedback_count=int(row["feedback_count"]),
                first_feedback_at=row["first_feedback_at"],
                scope_valid=row["scope_valid"] is True,
            )
            for row in inbox
        )
        links_by_finding: dict[tuple[str, int, str], list[str]] = {}
        for link in report_links:
            links_by_finding.setdefault(
                (str(link["report_id"]), int(link["report_version"]), str(link["finding_id"])),
                [],
            ).append(str(link["issue_id"]))
        for row in reports:
            document = dict(row["payload"])
            metrics = list(document.pop("metrics", ()))
            findings = list(document.pop("findings", ()))
            recommendations = list(document.pop("recommendations", ()))
            document["counts"] = {
                "metrics": len(metrics),
                "findings": len(findings),
                "recommendations": len(recommendations),
            }
            report_id = str(row["report_id"])
            report_version = int(row["report_version"])
            values.append(FaeReportProjection(
                projection_kind="fae_report_header_projection",
                report_id=report_id, report_version=report_version,
                item_id="header", occurred_at=row["generated_at"], payload=document,
            ))
            for metric in metrics:
                values.append(FaeReportProjection(
                    projection_kind="fae_report_metric_projection",
                    report_id=report_id, report_version=report_version,
                    item_id=str(metric["metric_id"]), occurred_at=row["generated_at"],
                    payload=dict(metric),
                ))
            for finding_value in findings:
                finding = dict(finding_value)
                linked = links_by_finding.get(
                    (report_id, report_version, str(finding["finding_id"])), ()
                )
                finding["linked_issue_ids"] = sorted(
                    set(finding.get("linked_issue_ids", ())) | set(linked)
                )
                values.append(FaeReportProjection(
                    projection_kind="fae_report_finding_projection",
                    report_id=report_id, report_version=report_version,
                    item_id=str(finding["finding_id"]), occurred_at=row["generated_at"],
                    payload=finding,
                ))
            for recommendation in recommendations:
                values.append(FaeReportProjection(
                    projection_kind="fae_report_recommendation_projection",
                    report_id=report_id, report_version=report_version,
                    item_id=str(recommendation["recommendation_id"]),
                    occurred_at=row["generated_at"], payload=dict(recommendation),
                ))
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
                    event_family=event.event_family,
                    severity=event.severity,
                    status=event.status,
                    title=event.title,
                    summary=event.summary,
                    source_kind=event.source_kind,
                    occurred_at=event.occurred_at,
                )
                for event in page.items
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
        ) as connection, connection.transaction():
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
                    feedback_sentiments=tuple(row.get("feedback_sentiments") or ()),
                    review_statuses=tuple(row.get("review_statuses") or ()),
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
