"""Admission metadata is not execution evidence. All authority remains in Attempt."""

import logging

from .turn_attempts import Lease

logger = logging.getLogger(__name__)


def conversation_is_held(connection, conversation_id, attempt_id, user_seq):
    return (
        connection.execute(
            "select 1 from platform_control.turn_attempts a "
            "join platform_control.conversation_turns t using(turn_id) "
            "join platform_control.conversation_messages m on m.message_id=t.user_message_id "
            "where t.conversation_id=%s and a.attempt_id<>%s and "
            "(a.status in ('running','reconciling') or (a.status='queued' and m.seq<%s)) limit 1",
            (conversation_id, attempt_id, user_seq),
        ).fetchone()
        is not None
    )


def claim_direct(repository, executor_id, lease_seconds):
    # Discovery's connection closes before candidate transactions start. Never
    # retain a skipped conversation's locks while inspecting the next one.
    with repository.transaction() as connection:
        candidates = connection.execute(
            "select a.attempt_id,t.conversation_id from platform_control.turn_attempts a "
            "join platform_control.conversation_turns t using(turn_id) "
            "join platform_control.conversations c using(conversation_id) "
            "where a.executor_kind='worker_direct' and c.execution_owner='worker_direct' "
            "and c.mode='direct_agent' and c.direct_agent_id='hr-bot' and c.status='active' "
            "and coalesce(to_jsonb(t)->>'execution_owner',c.execution_owner)=c.execution_owner "
            "and coalesce((to_jsonb(t)->>'origin_route_epoch')::bigint,c.route_epoch)=c.route_epoch "
            "and ((a.status='queued' and "
            "(a.cancel_requested_at is not null or a.readiness_check_after is null or "
            "a.readiness_check_after<=clock_timestamp())) or "
            "(a.status in ('running','reconciling') and a.lease_expires_at<=clock_timestamp())) "
            "order by (a.cancel_requested_at is not null or a.status<>'queued') desc,"
            "a.readiness_check_after nulls first,a.created_at,a.attempt_id limit 16 for update of c skip locked"
        ).fetchall()
    for candidate in candidates:
        with repository.transaction() as connection:
            c = connection.execute(
                "select * from platform_control.conversations where conversation_id=%s "
                "for update skip locked",
                (candidate["conversation_id"],),
            ).fetchone()
            if (
                c is None
                or c["execution_owner"] != "worker_direct"
                or c["status"] != "active"
                or c["mode"] != "direct_agent"
                or c["direct_agent_id"] != "hr-bot"
            ):
                continue
            t = connection.execute(
                "select t.*,m.seq as user_seq from platform_control.conversation_turns t "
                "join platform_control.conversation_messages m on m.message_id=t.user_message_id "
                "where t.turn_id=(select turn_id from platform_control.turn_attempts where attempt_id=%s) "
                "and t.conversation_id=%s for update of t skip locked",
                (candidate["attempt_id"], c["conversation_id"]),
            ).fetchone()
            if t is None:
                continue
            a = connection.execute(
                "select * from platform_control.turn_attempts where attempt_id=%s for update skip locked",
                (candidate["attempt_id"],),
            ).fetchone()
            if a is None:
                continue
            now = connection.execute("select clock_timestamp() as now").fetchone()[
                "now"
            ]
            initial = a["status"] == "queued" and a["cancel_requested_at"] is None
            if (
                a["executor_kind"] != "worker_direct"
                or a["status"] not in {"queued", "running", "reconciling"}
                or (a["status"] != "queued" and a["lease_expires_at"] > now)
                or t.get("execution_owner", c["execution_owner"])
                != c["execution_owner"]
                or t.get("origin_route_epoch", c["route_epoch"]) != c["route_epoch"]
            ):
                continue
            admission = None
            if initial:
                if t["status"] not in {"accepted", "running"} or (
                    a["readiness_check_after"] is not None
                    and a["readiness_check_after"] > now
                ):
                    continue
                # Rotate every inspected initial candidate, including held work,
                # so a bounded prefix cannot permanently starve another conversation.
                connection.execute(
                    "update platform_control.turn_attempts set readiness_check_after="
                    "clock_timestamp()+interval '30 seconds' where attempt_id=%s",
                    (a["attempt_id"],),
                )
                if conversation_is_held(
                    connection, c["conversation_id"], a["attempt_id"], t["user_seq"]
                ):
                    continue
                worker = connection.execute(
                    "select worker_id,v5_observation from platform_control.execution_workers "
                    "where status='active' and 'hr-bot'=any(allowed_agent_ids) "
                    "and v5_observation->>'ready'='true' "
                    "and (v5_observation->'service'->>'contractVersion')=any(case when %s then array['core_chat_collaboration_v6','core_chat_collaboration_v7'] else array['core_chat_collaboration_v5'] end) "
                    "and (v5_observation->>'expiresAt')::timestamptz>clock_timestamp() "
                    "order by worker_id limit 1 for share skip locked",
                    (bool(t.get('hr_input_context')),)
                ).fetchone()
                if worker is None:
                    warning = connection.execute(
                        "update platform_control.turn_attempts set reason_code='executor_capability_missing',"
                        "capability_missing_since=coalesce(capability_missing_since,clock_timestamp()),"
                        "capability_alerted_at=case when capability_alerted_at is null and "
                        "capability_missing_since<=clock_timestamp()-interval '5 minutes' "
                        "then clock_timestamp() else capability_alerted_at end where attempt_id=%s "
                        "returning capability_alerted_at",
                        (a["attempt_id"],),
                    ).fetchone()
                    if (
                        a["capability_alerted_at"] is None
                        and warning["capability_alerted_at"] is not None
                    ):
                        logger.warning(
                            "executor_capability_missing",
                            extra={"attempt_id": str(a["attempt_id"])},
                        )
                    if a["reason_code"] != "executor_capability_missing":
                        connection.execute("update platform_control.conversations set snapshot_version=snapshot_version+1 where conversation_id=%s", (c["conversation_id"],))
                    continue
                admission = {
                    "workerId": worker["worker_id"],
                    **worker["v5_observation"],
                }
            row = connection.execute(
                "update platform_control.turn_attempts set status=%s,executor_id=%s,"
                "lease_epoch=lease_epoch+1,lease_expires_at=clock_timestamp()+make_interval(secs=>%s),"
                "updated_at=clock_timestamp(),reason_code=case when %s then null else reason_code end,"
                "readiness_check_after=null,capability_missing_since=null,capability_alerted_at=null "
                "where attempt_id=%s and (%s::text is null or exists(select 1 from platform_control.execution_workers "
                "where worker_id=%s and status='active' and 'hr-bot'=any(allowed_agent_ids) "
                "and v5_observation->>'ready'='true' and (v5_observation->>'expiresAt')::timestamptz>clock_timestamp())) returning *",
                (
                    "running" if initial else "reconciling",
                    str(executor_id),
                    lease_seconds,
                    initial,
                    a["attempt_id"],
                    admission["workerId"] if admission else None,
                    admission["workerId"] if admission else None,
                ),
            ).fetchone()
            if row is None:
                continue
            connection.execute("update platform_control.conversations set snapshot_version=snapshot_version+1 where conversation_id=%s", (c["conversation_id"],))
            return Lease(
                row["attempt_id"],
                "worker_direct",
                executor_id,
                row["lease_epoch"],
                row["lease_expires_at"],
                row["status"],
                admission,
            )
    return None
