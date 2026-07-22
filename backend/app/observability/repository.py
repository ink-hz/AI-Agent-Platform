from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from app.fleet.catalog import AgentCatalog

from .models import (
    AgentSummary,
    EvidenceSummary,
    FeedbackItem,
    FlywheelFilters,
    FlywheelOverview,
    Freshness,
    ImprovementItem,
    Page,
    ReviewItem,
    SessionDetail,
    SessionFilters,
    SessionSummary,
    SyncStatus,
    TraceDetail,
    TraceStep,
    TurnDetail,
)


class ObservabilityReadError(RuntimeError):
    pass


class ObservabilityRepository(Protocol):
    def list_agents(self) -> tuple[AgentSummary, ...]: ...
    def get_agent(self, agent_id: str) -> AgentSummary | None: ...
    def list_sessions(self, filters: SessionFilters, limit: int, offset: int) -> Page[SessionSummary]: ...
    def get_session(self, session_key: str) -> SessionDetail | None: ...
    def get_trace(self, turn_key: str) -> TraceDetail | None: ...
    def get_flywheel_overview(self) -> FlywheelOverview: ...
    def list_improvement_items(self, filters: FlywheelFilters, limit: int, offset: int) -> Page[ImprovementItem]: ...
    def get_sync_status(self) -> tuple[SyncStatus, ...]: ...


def _freshness(source_kind: str, synced_at: datetime | None, now: datetime) -> Freshness:
    if source_kind == "metabot":
        return "live"
    if synced_at is None or now - synced_at > timedelta(hours=36):
        return "stale"
    return "fresh"


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


class PsycopgObservabilityRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable = psycopg.connect,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        catalog: AgentCatalog | None = None,
    ) -> None:
        self._database_url = database_url
        self._connect = connect
        self._now = now
        self._catalog = catalog or AgentCatalog.default()

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def list_agents(self) -> tuple[AgentSummary, ...]:
        statement = """
        select s.agent_id, min(s.source_kind) as source_kind,
          count(*)::bigint as session_count,
          (select count(*) from platform_read.turns t
             where t.agent_id=s.agent_id and nullif(btrim(t.answer), '') is not null)::bigint as total_turns,
          max(last_active_at) as last_activity_at,
          max(source_synced_at) as last_synced_at
        from platform_read.sessions s
        group by s.agent_id
        order by total_turns desc, s.agent_id
        """
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute(statement).fetchall()
            now = self._now()
            result: list[AgentSummary] = []
            seen: set[str] = set()
            for row in rows:
                profile = self._catalog.profile(row["agent_id"], row["agent_id"])
                source = row["source_kind"]
                seen.add(row["agent_id"])
                result.append(AgentSummary(
                    id=row["agent_id"],
                    name=profile.name,
                    domain=profile.domain,
                    description=profile.description,
                    glyph=profile.glyph,
                    accent=profile.accent,
                    visibility=profile.visibility,
                    source_kind=source,
                    deployment="Local" if source == "metabot" else "Alibaba Cloud",
                    session_count=int(row["session_count"]),
                    total_turns=int(row["total_turns"]),
                    last_activity_at=row["last_activity_at"],
                    last_synced_at=row["last_synced_at"],
                    freshness=_freshness(source, row["last_synced_at"], now),
                ))
            for profile in self._catalog.all_profiles():
                if profile.id in seen:
                    continue
                source = (
                    "fae" if profile.id == "ai-fae-agent"
                    else "admin" if profile.id == "ai-admin-agent"
                    else "metabot"
                )
                result.append(AgentSummary(
                    id=profile.id,
                    name=profile.name,
                    domain=profile.domain,
                    description=profile.description,
                    glyph=profile.glyph,
                    accent=profile.accent,
                    visibility=profile.visibility,
                    source_kind=source,
                    deployment="Local" if source == "metabot" else "Alibaba Cloud",
                    session_count=0,
                    total_turns=0,
                    freshness=_freshness(source, None, now),
                ))
            return tuple(sorted(result, key=lambda item: (-item.total_turns, item.id)))
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def get_agent(self, agent_id: str) -> AgentSummary | None:
        return next((agent for agent in self.list_agents() if agent.id == agent_id), None)

    def _session_conditions(self, filters: SessionFilters) -> tuple[str, list]:
        conditions = ["true"]
        params: list = []
        if not filters.agent_id:
            business_ids = list(self._catalog.ids_for_visibility("business"))
            if business_ids:
                conditions.append("s.agent_id = any(%s)")
                params.append(business_ids)
            else:
                conditions.append("false")
        for column, value in (
            ("s.agent_id", filters.agent_id),
            ("s.source_kind", filters.source_kind),
            ("s.channel", filters.channel),
            ("s.latest_outcome", filters.outcome),
        ):
            if value:
                conditions.append(f"{column} = %s")
                params.append(value)
        if filters.query:
            conditions.append("""
              (s.title ilike %s or exists (
                select 1 from platform_read.turns t where t.session_key=s.session_key
                  and (t.question ilike %s or t.answer ilike %s)
              ))
            """)
            pattern = f"%{filters.query}%"
            params.extend((pattern, pattern, pattern))
        if filters.sentiment:
            conditions.append("""
              exists (select 1 from platform_read.turns t
                join platform_read.feedback f on f.turn_key=t.turn_key
                where t.session_key=s.session_key and f.sentiment=%s)
            """)
            params.append(filters.sentiment)
        if filters.review_status:
            conditions.append("""
              exists (select 1 from platform_read.turns t
                join platform_read.reviews r on r.turn_key=t.turn_key
                where t.session_key=s.session_key and r.status=%s)
            """)
            params.append(filters.review_status)
        if filters.date_from:
            conditions.append("s.last_active_at >= %s")
            params.append(filters.date_from)
        if filters.date_to:
            conditions.append("s.last_active_at <= %s")
            params.append(filters.date_to)
        return " and ".join(conditions), params

    def _session_summary(self, row: dict) -> SessionSummary:
        return SessionSummary(
            session_key=row["session_key"],
            agent_id=row["agent_id"],
            source_kind=row["source_kind"],
            channel=row["channel"],
            title=row["title"],
            created_at=row["created_at"],
            last_active_at=row["last_active_at"],
            turn_count=int(row["turn_count"]),
            feedback_count=int(row["feedback_count"]),
            review_count=int(row["review_count"]),
            latest_outcome=row["latest_outcome"],
            source_synced_at=row["source_synced_at"],
            freshness=_freshness(row["source_kind"], row["source_synced_at"], self._now()),
        )

    def list_sessions(
        self,
        filters: SessionFilters,
        limit: int,
        offset: int,
    ) -> Page[SessionSummary]:
        where, params = self._session_conditions(filters)
        count_sql = f"select count(*) as count from platform_read.sessions s where {where}"
        list_sql = f"""
          select s.* from platform_read.sessions s where {where}
          order by s.last_active_at desc, s.session_key
          limit %s offset %s
        """
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                total = int(cursor.execute(count_sql, tuple(params)).fetchone()["count"])
                rows = cursor.execute(list_sql, tuple(params) + (limit, offset)).fetchall()
            return Page[SessionSummary](
                items=[self._session_summary(row) for row in rows],
                total=total,
                limit=limit,
                offset=offset,
            )
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def get_session(self, session_key: str) -> SessionDetail | None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                session = cursor.execute(
                    "select * from platform_read.sessions where session_key=%s",
                    (session_key,),
                ).fetchone()
                if session is None:
                    return None
                turns = cursor.execute(
                    "select * from platform_read.turns where session_key=%s order by turn_index, created_at",
                    (session_key,),
                ).fetchall()
                feedback_rows = cursor.execute(
                    """select f.* from platform_read.feedback f join platform_read.turns t
                       on t.turn_key=f.turn_key where t.session_key=%s order by f.created_at""",
                    (session_key,),
                ).fetchall()
                review_rows = cursor.execute(
                    """select r.* from platform_read.reviews r join platform_read.turns t
                       on t.turn_key=r.turn_key where t.session_key=%s order by r.created_at""",
                    (session_key,),
                ).fetchall()
                improvement_rows = cursor.execute(
                    """select i.* from platform_read.improvement_items i join platform_read.turns t
                       on t.turn_key=i.turn_key where t.session_key=%s order by i.created_at""",
                    (session_key,),
                ).fetchall()
            feedback = self._feedback_by_turn(feedback_rows)
            reviews = self._reviews_by_turn(review_rows)
            improvements = self._improvements_by_turn(improvement_rows)
            turn_models = [
                self._turn_detail(
                    row,
                    feedback.get(row["turn_key"], []),
                    reviews.get(row["turn_key"], []),
                    improvements.get(row["turn_key"], []),
                )
                for row in turns
            ]
            return SessionDetail(**self._session_summary(session).model_dump(), turns=turn_models)
        except ObservabilityReadError:
            raise
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def _feedback_by_turn(self, rows) -> dict[str, list[FeedbackItem]]:
        result: defaultdict[str, list[FeedbackItem]] = defaultdict(list)
        for row in rows:
            result[row["turn_key"]].append(FeedbackItem(
                feedback_key=row["feedback_key"], sentiment=row["sentiment"],
                raw_rating=row["raw_rating"], reason_code=row["reason_code"],
                comment=row["comment"], created_at=row["created_at"],
                details=_dict(row.get("details")),
            ))
        return result

    def _reviews_by_turn(self, rows) -> dict[str, list[ReviewItem]]:
        result: defaultdict[str, list[ReviewItem]] = defaultdict(list)
        for row in rows:
            result[row["turn_key"]].append(ReviewItem(
                review_key=row["review_key"], status=row["status"],
                native_priority=row["native_priority"],
                normalized_priority=row["normalized_priority"],
                failure_layer=row["failure_layer"], notes=row["notes"],
                corrected_answer=row["corrected_answer"], reviewer=row["reviewer"],
                created_at=row["created_at"], updated_at=row["updated_at"],
                details=_dict(row.get("details")),
            ))
        return result

    def _improvement(self, row) -> ImprovementItem:
        return ImprovementItem(
            item_key=row["item_key"], turn_key=row["turn_key"], agent_id=row["agent_id"],
            source_kind=row["source_kind"], item_type=row["item_type"], status=row["status"],
            priority=row["priority"], title=row["title"], summary=row["summary"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            source_synced_at=row["source_synced_at"], details=_dict(row.get("details")),
        )

    def _improvements_by_turn(self, rows) -> dict[str, list[ImprovementItem]]:
        result: defaultdict[str, list[ImprovementItem]] = defaultdict(list)
        for row in rows:
            if row["turn_key"]:
                result[row["turn_key"]].append(self._improvement(row))
        return result

    def _turn_detail(self, row, feedback, reviews, improvements) -> TurnDetail:
        sources = _list(row.get("sources"))
        evidence = [
            EvidenceSummary(
                kind=str(item.get("kind") or "source"),
                title=str(item.get("title") or item.get("name") or item.get("source") or "Source"),
                reference=item.get("url") or item.get("ref") or item.get("path"),
                metadata={key: value for key, value in item.items() if key not in {"title", "name", "url", "ref", "path"}},
            )
            for item in sources if isinstance(item, dict)
        ]
        availability = "restricted" if row["source_kind"] == "metabot" else "available"
        return TurnDetail(
            turn_key=row["turn_key"], session_key=row["session_key"], agent_id=row["agent_id"],
            source_kind=row["source_kind"], turn_index=int(row["turn_index"]),
            question=row["question"], answer=row["answer"], created_at=row["created_at"],
            trace_key=row["trace_key"], outcome=row["outcome"], fallback_used=row["fallback_used"],
            duration_ms=row["duration_ms"], sources=sources, evidence=evidence,
            evidence_availability=availability, feedback=feedback, reviews=reviews,
            improvements=improvements, details=_dict(row.get("details")),
        )

    def get_trace(self, turn_key: str) -> TraceDetail | None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                trace = cursor.execute(
                    "select * from platform_read.traces where turn_key=%s",
                    (turn_key,),
                ).fetchone()
                if trace is None:
                    return None
                rows = cursor.execute(
                    """select * from platform_read.trace_steps where trace_key=%s
                       order by started_at nulls last, seq nulls last, step_key""",
                    (trace["trace_key"],),
                ).fetchall()
            steps = [TraceStep(
                step_key=row["step_key"], trace_key=row["trace_key"], kind=row["kind"],
                name=row["name"], status=row["status"], parent_step_key=row["parent_step_key"],
                seq=row["seq"], started_at=row["started_at"], duration_ms=row["duration_ms"],
                input_summary=_dict(row.get("input_summary")),
                output_summary=_dict(row.get("output_summary")),
                safe_metadata=_dict(row.get("safe_metadata")),
                error_summary=row["error_summary"],
            ) for row in rows]
            return TraceDetail(
                trace_key=trace["trace_key"], turn_key=trace["turn_key"],
                agent_id=trace["agent_id"], source_kind=trace["source_kind"],
                status=trace["status"], started_at=trace["started_at"],
                completed_at=trace["completed_at"], duration_ms=trace["duration_ms"],
                engine=trace["engine"], backend=trace["backend"], model=trace["model"],
                input_tokens=trace["input_tokens"], output_tokens=trace["output_tokens"],
                cost_usd=float(trace["cost_usd"]) if trace["cost_usd"] is not None else None,
                error_class=trace["error_class"], error_message=trace["error_message"],
                detail_availability=trace["detail_availability"],
                source_synced_at=trace["source_synced_at"],
                details=_dict(trace.get("details")), steps=steps,
            )
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def get_flywheel_overview(self) -> FlywheelOverview:
        statement = """
        select
          (select count(*) from platform_read.feedback)::bigint as feedback_total,
          (select count(*) from platform_read.feedback where sentiment='negative')::bigint as negative_feedback,
          (select count(*) from platform_read.feedback f
             left join platform_read.reviews r on r.turn_key=f.turn_key
             where f.sentiment='negative' and r.review_key is null)::bigint as pending_reviews,
          (select count(*) from platform_read.improvement_items where item_type='evaluation')::bigint as evaluation_candidates,
          (select count(*) from platform_read.improvement_items where item_type='knowledge')::bigint as knowledge_tasks,
          (select count(*) from platform_read.improvement_items where item_type='qa')::bigint as qa_candidates
        """
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(statement).fetchone()
            return FlywheelOverview(**{key: int(value) for key, value in row.items()})
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def list_improvement_items(self, filters: FlywheelFilters, limit: int, offset: int) -> Page[ImprovementItem]:
        conditions = ["true"]
        params: list = []
        for column, value in (("agent_id", filters.agent_id), ("item_type", filters.item_type), ("status", filters.status)):
            if value:
                conditions.append(f"{column}=%s")
                params.append(value)
        where = " and ".join(conditions)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                total = int(cursor.execute(
                    f"select count(*) as count from platform_read.improvement_items where {where}",
                    tuple(params),
                ).fetchone()["count"])
                rows = cursor.execute(
                    f"""select * from platform_read.improvement_items where {where}
                        order by updated_at desc, item_key limit %s offset %s""",
                    tuple(params) + (limit, offset),
                ).fetchall()
            return Page[ImprovementItem](items=[self._improvement(row) for row in rows], total=total, limit=limit, offset=offset)
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error

    def get_sync_status(self) -> tuple[SyncStatus, ...]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                rows = cursor.execute("select * from platform_read.sync_status order by source_kind").fetchall()
            now = self._now()
            statuses = []
            for row in rows:
                last_success_at = row.get("last_success_at")
                if last_success_at is None and row["status"] == "succeeded":
                    last_success_at = row["completed_at"]
                statuses.append(SyncStatus(
                    source_kind=row["source_kind"], status=row["status"],
                    started_at=row["started_at"], completed_at=row["completed_at"],
                    last_success_at=last_success_at,
                    source_counts=_dict(row["source_counts"]),
                    applied_counts=_dict(row["applied_counts"]),
                    validation=_dict(row["validation"]),
                    error_summary=row["error_summary"],
                    freshness=_freshness(row["source_kind"], last_success_at, now),
                ))
            return tuple(statuses)
        except Exception as error:
            raise ObservabilityReadError("observability query failed") from error


class UnavailableObservabilityRepository:
    def _raise(self, *_args, **_kwargs):
        raise ObservabilityReadError("observability query failed")

    def __getattr__(self, _name):
        return self._raise
