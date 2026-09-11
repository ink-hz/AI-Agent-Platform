"""Authenticated immutable source evidence, never current business-write authority."""

import json

from .content_crypto import SealedContent
from .contracts_v5 import CallbackAckV5
from .core_contract import parse_core_event


def accept_source(bindings, worker_id, run_id, body):
    if type(body) is not bytes or not body or len(body) > 1_048_576:
        raise ValueError("v5 source invalid")
    raw = json.loads(body)
    event = parse_core_event(raw)
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
        frozen = bindings._decode({**job,**row}).frozen.document
        if event.contract_version != frozen["contractVersion"]:
            raise ValueError("source contract mismatch")
        if event.contract_version in {'core_chat_collaboration_v6','core_chat_collaboration_v7'} and event.event_type=="result":
            for ref in event.payload.result_refs:
                found=connection.execute("select 1 from platform_hr.tool_operations_v6 where receipt_id=%s "
                    "and turn_id=%s and tool='hr.submit_result' and schema_id=%s and content_sha256=%s",
                    (ref.result_id,frozen['turnId'],ref.schema_id,ref.content_sha256)).fetchone()
                if found is None:
                    raise ValueError("result_unregistered")
        knowledge_read = None
        if (event.contract_version in {'core_chat_collaboration_v6','core_chat_collaboration_v7'} and event.event_type=='raw_progress'
            and event.payload.source_ref=='hr:knowledge-read:v1'):
            import re
            knowledge_read=json.loads(event.payload.text)
            if (event.payload.source!='agent_runtime' or event.payload.kind!='state'
                or type(knowledge_read) is not dict
                or set(knowledge_read)!={'format','teamCommit','methodId','revision','sha256','toolUseId'}
                or knowledge_read['format']!='hr-knowledge-read-v1'
                or knowledge_read['teamCommit']!=frozen['rolePackage']['teamCommit']
                or any(not isinstance(knowledge_read[k],str) for k in knowledge_read)
                or re.fullmatch('[a-z0-9-]{1,128}',knowledge_read['methodId']) is None
                or re.fullmatch('[A-Za-z0-9._-]{1,128}',knowledge_read['revision']) is None
                or re.fullmatch('[a-f0-9]{64}',knowledge_read['sha256']) is None
                or not 1<=len(knowledge_read['toolUseId'])<=256):
                raise ValueError('knowledge read evidence invalid')
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
        if knowledge_read is not None:
            from psycopg.types.json import Jsonb
            connection.execute('insert into platform_hr.knowledge_reads_v6(run_id,seq,turn_id,proof) values(%s,%s,%s,%s)',
                (run_id,event.seq,frozen['turnId'],Jsonb(knowledge_read)))
        cursor = event.seq
        return ack("accepted")
