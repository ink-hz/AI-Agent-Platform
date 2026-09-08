"""Authenticated immutable source evidence, never current business-write authority."""

import json

from .content_crypto import SealedContent
from .contracts_v5 import CallbackAckV5, parse_v5_event


def accept_source(bindings, worker_id, run_id, body):
    if type(body) is not bytes or not body or len(body) > 1_048_576:
        raise ValueError("v5 source invalid")
    raw = json.loads(body)
    event = parse_v5_event(raw)
    with bindings.relay._connection() as connection:
        row = connection.execute(
            "select b.* from platform_control.direct_command_bindings b "
            "join platform_control.execution_jobs j using(job_id) where j.run_id=%s for update of b",
            (run_id,),
        ).fetchone()
        if row is None:
            raise PermissionError("v5 source unauthorized")
        job = connection.execute(
            "select * from platform_control.execution_jobs where job_id=%s and job_kind='worker_direct_v5' for update",
            (row["job_id"],),
        ).fetchone()
        if (
            job is None
            or row["transport_worker_id"] != worker_id
            or row["launch_lease_epoch"] is None
            or row["retired_unsent_at"] is not None
            or connection.execute(
                "select 1 from platform_control.execution_workers where worker_id=%s "
                "and status='active' and 'hr-bot'=any(allowed_agent_ids)",
                (worker_id,),
            ).fetchone()
            is None
        ):
            raise PermissionError("v5 source unauthorized")
        if (event.run_id, event.command_id, event.attempt_id, event.lease_epoch) != (
            run_id,
            row["command_id"],
            row["attempt_id"],
            row["launch_lease_epoch"],
        ):
            raise ValueError("v5 source identity invalid")
        summary = connection.execute(
            "select coalesce(max(seq),0) as cursor,bool_or(event_type not in ('raw_progress','run_heartbeat')) as terminal "
            "from platform_control.v5_source_events where run_id=%s",
            (run_id,),
        ).fetchone()
        cursor = summary["cursor"]

        def ack(status):
            return CallbackAckV5(
                status=status,
                run_id=run_id,
                accepted_through=cursor,
                expected_seq=cursor + 1,
            )

        previous = connection.execute(
            "select * from platform_control.v5_source_events where run_id=%s and seq=%s",
            (run_id, event.seq),
        ).fetchone()
        if previous:
            stored = bindings.relay.content_codec.unseal_json(
                f"execution-v5-source:{run_id}:{event.seq}",
                SealedContent(
                    bytes(previous["payload_ciphertext"]),
                    previous["encryption_key_version"],
                ),
            )
            return ack("duplicate" if json.loads(stored["raw"]) == raw else "conflict")
        if summary["terminal"] or event.seq <= cursor or event.seq >= 9007199254740991:
            return ack("conflict")
        if event.seq != cursor + 1:
            return ack("gap")
        sealed = bindings.relay.content_codec.seal_json(
            f"execution-v5-source:{run_id}:{event.seq}",
            {"raw": body.decode("utf-8")},
        )
        connection.execute(
            "insert into platform_control.v5_source_events(run_id,seq,event_type,payload_ciphertext,encryption_key_version) "
            "values(%s,%s,%s,%s,%s)",
            (
                run_id,
                event.seq,
                event.event_type,
                sealed.ciphertext,
                sealed.key_version,
            ),
        )
        cursor = event.seq
        return ack("accepted")
