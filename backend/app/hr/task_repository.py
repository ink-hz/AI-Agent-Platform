from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .task_service import HrPositionTask, HrPositionTaskUnavailable, HrTaskReference

_TASK_PROJECTION_CTE = """
    with scoped_turns as (
      select binding.owner_internal_user_id,binding.position_id,
             turn.client_request_id,binding.conversation_id,turn.turn_id,
             turn.mission_id,turn.status as turn_status,turn.updated_at
      from platform_hr.position_conversations binding
      join platform_control.conversations conversation
        on conversation.conversation_id=binding.conversation_id
       and conversation.owner_internal_user_id=binding.owner_internal_user_id
       and conversation.mode='direct_agent'
       and conversation.direct_agent_id='hr-bot'
      join platform_control.conversation_turns turn
        on turn.conversation_id=conversation.conversation_id
    ), projected as (
      select request.task_request_id as task_id,request.task_kind,
             request.candidate_id,request.position_candidate_id,
             scoped.conversation_id,scoped.turn_id,request.created_at,
             record.task_record_id,
             record.official_position_version_id,record.context_version_id,
             coalesce(record.material_attachment_ids,'{}'::uuid[])
               as material_attachment_ids,
             official.source_version as official_source_version,
             official.source_snapshot_at as official_freshness,
             context.version_number as context_version_number,
             retrieval.retrieval_id as panorama_retrieval_id,
             retrieval.insight_version_ids as panorama_insight_version_ids,
             retrieval.retrieved_excerpts as panorama_retrieved_excerpts,
             coalesce(scoped.updated_at,request.created_at) as updated_at,
             case
               when scoped.turn_status in ('failed','cancelled','interrupted')
                 or mission.status in ('failed','cancelled','interrupted')
                 or execution.failed then 'failed'
               when request.task_kind in (
                   'jd','jr','talent_profile','sourcing_strategy',
                   'position_interview_plan','candidate_match',
                   'candidate_interview_plan'
                 ) and projection.state='failed' then 'failed'
               when scoped.turn_status='completed'
                 or mission.status in ('completed','partially_completed')
                 then case
                   when request.task_kind not in (
                     'jd','jr','talent_profile','sourcing_strategy',
                     'position_interview_plan','candidate_match',
                     'candidate_interview_plan'
                   ) then 'completed'
                   when projection.state='completed' then 'completed'
                   else 'running'
                 end
               when scoped.turn_status in (
                   'running','waiting_agents','waiting_user','completing'
                 ) or mission.status in ('delegated','synthesizing')
                 or execution.running then 'running'
               else 'accepted'
             end as status,
             case
               when scoped.turn_status='cancelled' or mission.status='cancelled'
                 or execution.cancelled then 'cancelled'
               when scoped.turn_status='interrupted' or mission.status='interrupted'
                 or execution.interrupted then 'interrupted'
               when scoped.turn_status='failed' or mission.status='failed'
                 or execution.failed then 'execution_failed'
               when request.task_kind in (
                   'jd','jr','talent_profile','sourcing_strategy',
                   'position_interview_plan','candidate_match',
                   'candidate_interview_plan'
                 ) and projection.state='failed'
                 then 'result_projection_failed'
               else null
             end as error
      from platform_hr.position_task_requests request
      join platform_hr.positions position
        on position.position_id=request.position_id
       and position.owner_internal_user_id=request.owner_internal_user_id
      left join scoped_turns scoped
        on scoped.owner_internal_user_id=request.owner_internal_user_id
       and scoped.position_id=request.position_id
       and scoped.client_request_id=request.client_request_id
      left join platform_control.missions mission
        on mission.mission_id=scoped.mission_id
       and mission.owner_internal_user_id=request.owner_internal_user_id
      left join platform_hr.position_task_records record
        on record.owner_internal_user_id=request.owner_internal_user_id
       and record.position_id=request.position_id
       and record.client_request_id=request.client_request_id
       and record.task_kind=request.task_kind
      left join platform_hr.official_position_versions official
        on official.owner_internal_user_id=request.owner_internal_user_id
       and official.position_id=request.position_id
       and official.official_position_version_id=
         record.official_position_version_id
      left join platform_hr.position_context_versions context
        on context.owner_internal_user_id=request.owner_internal_user_id
       and context.position_id=request.position_id
       and context.context_version_id=record.context_version_id
      left join lateral platform_hr.read_position_insight_retrieval_for_turn_v79(
        request.owner_internal_user_id,request.position_id,scoped.turn_id
      ) retrieval on true
      left join lateral platform_hr.read_hr_task_result_projection_state_v71(
        record.task_record_id
      ) projection on true
      left join lateral (
        select
          coalesce(bool_or(job.status in ('leased','dispatched','running')),false)
            as running,
          coalesce(bool_or(job.status='failed'),false) as failed,
          coalesce(bool_or(job.status='cancelled'),false) as cancelled,
          coalesce(bool_or(job.status='interrupted'),false) as interrupted
        from platform_control.mission_runs run
        join platform_control.execution_jobs job using (run_id)
        where run.mission_id=mission.mission_id
      ) execution on true
      where request.owner_internal_user_id=%s and request.position_id=%s
        and request.status in ('active','consumed')
    )
"""


def _freshness_date(value: object) -> str | None:
    if hasattr(value, "date"):
        return value.date().isoformat()
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return None


def _references(row: dict[str, Any]) -> tuple[HrTaskReference, ...]:
    references: list[HrTaskReference] = []
    if not isinstance(row.get("task_record_id"), UUID):
        return ()
    official_id = row.get("official_position_version_id")
    source_version = row.get("official_source_version")
    if isinstance(official_id, UUID):
        version = str(source_version) if source_version else None
        references.append(HrTaskReference(
            "official_position", official_id,
            f"官网岗位 · {version}" if version else "官网岗位版本",
            version, "岗位任务的官网基线",
            _freshness_date(row.get("official_freshness")),
        ))
    context_id = row.get("context_version_id")
    context_number = row.get("context_version_number")
    if isinstance(context_id, UUID):
        version = f"v{context_number}" if isinstance(context_number, int) else None
        references.append(HrTaskReference(
            "confirmed_context", context_id,
            f"岗位理解 {version}" if version else "已确认岗位理解",
            version, "任务启动时的已确认岗位理解", None,
        ))
    material_ids = row.get("material_attachment_ids") or ()
    for material_id in material_ids:
        if isinstance(material_id, UUID):
            references.append(HrTaskReference(
                "position_material", material_id,
                f"岗位材料 · {str(material_id)[:8]}", None,
                "本轮明确选择的岗位材料", None,
            ))
    candidate_id = row.get("candidate_id")
    if isinstance(candidate_id, UUID):
        references.append(HrTaskReference(
            "candidate_snapshot", candidate_id,
            f"候选人分析上下文 · {str(candidate_id)[:8]}", None,
            "任务启动时固定的候选人资料与分析上下文", None,
        ))
    retrieval_id = row.get("panorama_retrieval_id")
    if isinstance(retrieval_id, UUID):
        excerpts = row.get("panorama_retrieved_excerpts") or ()
        freshness = None
        if excerpts and isinstance(excerpts[0], dict):
            source_freshness = excerpts[0].get("freshness")
            if isinstance(source_freshness, dict):
                freshness = _freshness_date(source_freshness.get("as_of"))
        references.append(HrTaskReference(
            "panorama_insight", retrieval_id,
            f"全景招聘情报 · 截至 {freshness}" if freshness else "全景招聘情报",
            None, "与本岗位方向相关的招聘情报", freshness,
        ))
    return tuple(references)


def _task(row: dict[str, Any]) -> HrPositionTask:
    return HrPositionTask(
        task_id=row["task_id"],
        task_kind=row["task_kind"],
        status=row["status"],
        error=row["error"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        candidate_id=row["candidate_id"],
        position_candidate_id=row["position_candidate_id"],
        references=_references(row),
    )


class PostgresHrPositionTaskRepository:
    """Owner-scoped durable task recovery over existing HR and execution tables."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("HR position task database URL required")
        if not callable(connect):
            raise TypeError("HR position task database connection required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def position_exists(self, owner_id: UUID, position_id: UUID) -> bool:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise TypeError("HR position task identifiers invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select 1 as exists from platform_hr.positions where "
                    "owner_internal_user_id=%s and position_id=%s",
                    (owner_id, position_id),
                ).fetchone()
            return row is not None
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrPositionTaskUnavailable("position tasks unavailable") from None

    def recoverable_tasks(
        self, owner_id: UUID, position_id: UUID
    ) -> tuple[HrPositionTask, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise TypeError("HR position task identifiers invalid")
        query = _TASK_PROJECTION_CTE + """
            select task_id,task_kind,status,error,conversation_id,turn_id,
                   candidate_id,position_candidate_id,
                   task_record_id,official_position_version_id,context_version_id,
                   material_attachment_ids,official_source_version,
                   official_freshness,context_version_number,
                   panorama_retrieval_id,panorama_insight_version_ids,
                   panorama_retrieved_excerpts
            from projected
            where status in ('accepted','running')
               or updated_at > now()-interval '24 hours'
            order by case status when 'running' then 0 when 'accepted' then 1 else 2 end,
                     updated_at desc,task_id
            limit 50
        """
        try:
            with self._connection() as connection:
                rows = connection.execute(query, (owner_id, position_id)).fetchall()
            task_ids = [row["task_id"] for row in rows]
            if len(task_ids) != len(set(task_ids)):
                raise HrPositionTaskUnavailable("position tasks unavailable")
            return tuple(_task(row) for row in rows)
        except HrPositionTaskUnavailable:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrPositionTaskUnavailable("position tasks unavailable") from None

    def task(
        self, owner_id: UUID, position_id: UUID, task_id: UUID
    ) -> HrPositionTask | None:
        if any(not isinstance(value, UUID) for value in (owner_id, position_id, task_id)):
            raise TypeError("HR position task identifiers invalid")
        query = _TASK_PROJECTION_CTE + """
            select task_id,task_kind,status,error,conversation_id,turn_id,
                   candidate_id,position_candidate_id,
                   task_record_id,official_position_version_id,context_version_id,
                   material_attachment_ids,official_source_version,
                   official_freshness,context_version_number,
                   panorama_retrieval_id,panorama_insight_version_ids,
                   panorama_retrieved_excerpts
            from projected
            where task_id=%s
        """
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    query, (owner_id, position_id, task_id)
                ).fetchall()
            if len(rows) > 1:
                raise HrPositionTaskUnavailable("position task unavailable")
            return _task(rows[0]) if rows else None
        except HrPositionTaskUnavailable:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise HrPositionTaskUnavailable("position task unavailable") from None
