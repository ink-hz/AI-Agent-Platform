from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .task_service import HrPositionTask, HrPositionTaskUnavailable

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
                   candidate_id,position_candidate_id
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
                   candidate_id,position_candidate_id
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
