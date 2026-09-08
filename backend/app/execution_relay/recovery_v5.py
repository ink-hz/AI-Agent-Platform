"""Nondispatch bound-run recovery on the existing signed Worker authority.

Current cloud ownership authorizes inspection, never a launch. Original launch
and callback credentials remain those of the durable command transport.
"""

import asyncio
import json
from uuid import UUID

from app.agent_brain.direct_command_binding import BindingRejected
from app.agent_brain.turn_attempts import Lease, LeaseRejected

from .acceptance_v5 import parse_v5_acceptance
from .contracts_v5 import ExecutionRecoveryV5
from .core_contract import parse_core_command
from .frozen_command_v5 import hydrate_frozen_command
from .worker_v5_receiver import TrustedV5CallbackBinding


def bound_command(bindings, row):
    binding = bindings._decode(row)
    transport = bindings._wrapper(row).get("transport")
    if transport is None or row["retired_unsent_at"] is not None:
        raise BindingRejected()
    command = hydrate_frozen_command(
        binding.frozen,
        lease_epoch=transport["leaseEpoch"],
        event_callback_url=f"{transport['callbackOrigin']}/callbacks/{binding.run_id}/{transport['callbackToken']}",
        input_attachment_grants=bindings.materials(row)["inputAttachmentGrants"],
        output_write_grant=bindings.materials(row)["outputWriteGrant"],
        business_tool_grant=transport.get("businessToolGrant"),
    )
    return command, transport


def parse_observation(value, command):
    expected = {
        "version": "hr_bound_recovery_v1",
        "commandId": str(command.command_id),
        "runId": str(command.run_id),
        "commandHash": command.command_hash,
    }
    if (
        type(value) is not dict
        or set(value) != {*expected, "missing", "acceptance", "recovery"}
        or any(value[key] != member for key, member in expected.items())
        or type(value["missing"]) is not bool
    ):
        raise ValueError("v5 recovery invalid")
    if value["missing"]:
        if value["acceptance"] is not None or value["recovery"] is not None:
            raise ValueError("v5 recovery invalid")
        return None, None
    acceptance = parse_v5_acceptance(value["acceptance"], command)
    if acceptance.launch_lease_epoch is None:
        if value["recovery"] is not None:
            raise ValueError("v5 recovery invalid")
        return acceptance, None
    recovery = ExecutionRecoveryV5.model_validate(value["recovery"])
    if recovery.replay_used:
        raise ValueError("v5 recovery invalid")
    return acceptance, recovery


def _current(bindings, connection, worker_id, lease):
    _, _, attempt = bindings._lock(lease, connection)
    row = bindings._transport_row(lease, connection)
    if (
        attempt["status"] != "reconciling"
        or row is None
        or row["transport_worker_id"] != worker_id
        or row["offered_at"] is None
        or row["retired_unsent_at"] is not None
        or connection.execute(
            "select 1 from platform_control.execution_workers where worker_id=%s and status='active' and 'hr-bot'=any(allowed_agent_ids)",
            (worker_id,),
        ).fetchone()
        is None
    ):
        raise BindingRejected()
    return attempt, row


def recovery_work(bindings, worker_id):
    with bindings.relay._connection() as connection:
        with connection.transaction():
            candidates = connection.execute(
                "select a.attempt_id,a.executor_id,a.lease_epoch from platform_control.turn_attempts a "
                "join platform_control.direct_command_bindings b using(attempt_id) "
                "where a.status='reconciling' and a.lease_expires_at>clock_timestamp() "
                "and b.transport_worker_id=%s and b.offered_at is not null and b.retired_unsent_at is null "
                "and (b.recovery_poll_after is null or b.recovery_poll_after<=clock_timestamp()) "
                "order by b.recovery_poll_after nulls first,a.attempt_id limit 16",
                (worker_id,),
            ).fetchall()
        for candidate in candidates:
            try:
                with connection.transaction():
                    lease = Lease(
                        candidate["attempt_id"],
                        "worker_direct",
                        UUID(candidate["executor_id"]),
                        candidate["lease_epoch"],
                        None,
                        "reconciling",
                    )
                    attempt, row = _current(bindings, connection, worker_id, lease)
                    wrapper=bindings._wrapper(row)
                    transport=wrapper['transport']
                    if (wrapper['command']['contractVersion']=='core_chat_collaboration_v6'
                        and not attempt['cancel_requested_at']
                        and transport.get('businessToolLeaseEpoch')!=lease.lease_epoch):
                        from app.hr.tool_service import HrToolService
                        grant=HrToolService(bindings.relay).issue_grant(connection,lease.attempt_id,worker_id,lease.lease_epoch)
                        transport.update(leaseEpoch=lease.lease_epoch,executorId=str(lease.executor_id),
                            businessToolLeaseEpoch=lease.lease_epoch,businessToolGrant=grant.model_dump(mode='json',by_alias=True))
                        sealed=bindings.relay.content_codec.seal_json(f"execution-job:{row['job_id']}:{row['run_id']}",wrapper)
                        connection.execute('update platform_control.execution_jobs set payload_ciphertext=%s,encryption_key_version=%s where job_id=%s',
                            (sealed.ciphertext,sealed.key_version,row['job_id']))
                        row=bindings._transport_row(lease,connection)
                    command, transport = bound_command(bindings, row)
                    connection.execute(
                        "update platform_control.direct_command_bindings set recovery_poll_after=clock_timestamp()+interval '2 seconds' where attempt_id=%s",
                        (lease.attempt_id,),
                    )
                    return {
                        "version": "hr_bound_recovery_work_v1",
                        "workerId": worker_id,
                        "executorId": str(lease.executor_id),
                        "leaseEpoch": lease.lease_epoch,
                        "callbackOrigin": transport["callbackOrigin"],
                        "command": command.model_dump(mode="json", by_alias=True),
                        "stop": attempt["cancel_requested_at"] is not None,
                    }
            except (BindingRejected, LeaseRejected):
                continue
    return None


def record_recovery(bindings, worker_id, run_id, value):
    if (
        type(value) is not dict
        or set(value) != {"executorId", "leaseEpoch", "observation"}
        or type(value["leaseEpoch"]) is not int
        or value["leaseEpoch"] < 1
    ):
        raise ValueError("v5 recovery invalid")
    executor_id = UUID(value["executorId"])
    with bindings.relay._connection() as connection:
        candidate = connection.execute(
            "select b.attempt_id from platform_control.direct_command_bindings b join platform_control.execution_jobs j using(job_id) where j.run_id=%s",
            (run_id,),
        ).fetchone()
        if candidate is None:
            raise BindingRejected()
        lease = Lease(
            candidate["attempt_id"],
            "worker_direct",
            executor_id,
            value["leaseEpoch"],
            None,
            "reconciling",
        )
        _, row = _current(bindings, connection, worker_id, lease)
        command, _ = bound_command(bindings, row)
        acceptance, recovery = parse_observation(value["observation"], command)
        if acceptance and acceptance.launch_lease_epoch is not None:
            if row["launch_lease_epoch"] not in {None, acceptance.launch_lease_epoch}:
                raise ValueError("v5 recovery origin changed")
            connection.execute(
                "update platform_control.direct_command_bindings set accepted_at=coalesce(accepted_at,clock_timestamp()),launch_lease_epoch=coalesce(launch_lease_epoch,%s) where attempt_id=%s",
                (acceptance.launch_lease_epoch, lease.attempt_id),
            )
        proof = recovery.executor_stop_proof_ref if recovery else None
        if proof and row["executor_stop_proof_ref"] not in {None, proof}:
            raise ValueError("v5 recovery stop proof changed")
        connection.execute(
            "update platform_control.direct_command_bindings set recovery_observed_at=clock_timestamp(),recovery_reason=%s,executor_stop_proof_ref=coalesce(executor_stop_proof_ref,%s) where attempt_id=%s",
            (
                "executor_stopped"
                if proof
                else "execution_absent_unknown"
                if value["observation"]["missing"]
                else "executor_stop_unknown",
                proof,
                lease.attempt_id,
            ),
        )
    return True


class V5RecoveryAdapter:
    def __init__(self, runtime):
        self.runtime = runtime

    async def poll_once(self):
        runtime = self.runtime
        if not runtime.callback_ready.is_set() or not runtime.enable_v5_callbacks:
            return False
        work = await runtime.cloud.post_v5_bytes(
            "/api/v1/execution-worker/v5/recovery", b"{}"
        )
        if work.status_code == 204:
            return False
        if work.status_code != 200:
            return False
        item = work.json()
        if (
            type(item) is not dict
            or set(item)
            != {
                "version",
                "workerId",
                "executorId",
                "leaseEpoch",
                "callbackOrigin",
                "command",
                "stop",
            }
            or item["version"] != "hr_bound_recovery_work_v1"
            or item["workerId"] != runtime.worker_id
            or item["callbackOrigin"] != f"http://127.0.0.1:{runtime.callback_port}"
            or type(item["stop"]) is not bool
        ):
            raise ValueError("v5 recovery work invalid")
        command = parse_core_command(item["command"])
        if not command.event_callback_url.startswith(
            item["callbackOrigin"] + "/callbacks/"
        ):
            raise ValueError("v5 recovery callback invalid")
        observed = await asyncio.to_thread(
            runtime.metabot.recover_v5_run, item["command"], item["stop"]
        )
        acceptance, _ = parse_observation(observed, command)
        if acceptance and acceptance.launch_lease_epoch is not None:
            binding = TrustedV5CallbackBinding(
                runtime.worker_id,
                command.command_id,
                command.run_id,
                command.attempt_id,
                command.command_hash,
                acceptance.launch_lease_epoch,
                acceptance.transport_lease_epoch,
                item["callbackOrigin"],
            )
            await asyncio.to_thread(
                runtime.store.register_v5_callback, item["command"], binding
            )
        result = await runtime.cloud.post_v5_bytes(
            f"/api/v1/execution-worker/v5/runs/{command.run_id}/recovery",
            json.dumps(
                {
                    "executorId": item["executorId"],
                    "leaseEpoch": item["leaseEpoch"],
                    "observation": observed,
                },
                separators=(",", ":"),
            ).encode(),
        )
        return result.status_code == 200
