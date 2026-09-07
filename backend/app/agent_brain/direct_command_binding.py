"""Internal command storage joined to the existing Attempt transaction."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.execution_relay.acceptance_v5 import AcceptanceV5
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.frozen_command_v5 import FrozenCommandV5, parse_frozen_command

from .turn_attempts import LeaseRejected


@dataclass(frozen=True, repr=False)
class FrozenInput:
    owner_id: UUID
    conversation_updated_at: datetime
    turn_updated_at: datetime
    summary_through_seq: int
    user_message_id: UUID
    user_message_seq: int
    prompt: str
    context_hash: str
    tool_policy: str = "none"


def principal_reference(owner_id: UUID) -> str:
    """Stable pseudonym, not authorization; input must come from the locked owner row."""
    return (
        "principal:"
        + hashlib.sha256(b"hr-worker-principal-v1\0" + owner_id.bytes).hexdigest()
    )


@dataclass(frozen=True, repr=False)
class CommandBinding:
    attempt_id: UUID
    command_id: UUID
    run_id: UUID
    job_id: UUID
    command_seq: int
    frozen: FrozenCommandV5


class BindingRejected(RuntimeError):
    def __init__(self):
        super().__init__("direct command binding rejected")


class DirectCommandBindingRepository:
    def __init__(self, relay):
        self.relay = relay

    def get_prepared(self, lease, *, connection):
        """Read the frozen command before any context rebuild; never infers absence as stop."""
        c, t, a = self._lock(lease, connection)
        row = self._transport_row(lease, connection)
        if row is None:
            return None
        result = self._decode(row)
        value = result.frozen.document
        if (
            row["retired_unsent_at"] is not None
            or value["principalRef"] != principal_reference(c["owner_internal_user_id"])
            or value["conversationId"] != str(c["conversation_id"])
            or value["turnId"] != str(t["turn_id"])
            or value["triggerMessageId"] != str(t["user_message_id"])
            or value["attemptNo"] != a["attempt_no"]
            or result.run_id != a["transport_run_id"]
        ):
            raise BindingRejected()
        return result

    def prepare(self, lease, frozen_input, *, connection):
        c, t, a = self._lock(lease, connection)
        if (
            c["owner_internal_user_id"] != frozen_input.owner_id
            or t["user_message_id"] != frozen_input.user_message_id
            or t["user_seq"] != frozen_input.user_message_seq
        ):
            raise BindingRejected()
        row = self._transport_row(lease, connection)
        if row is not None:
            result = self._decode(row)
            value = result.frozen.document
            if (
                row["retired_unsent_at"] is not None
                or value["prompt"] != frozen_input.prompt
                or value["contextHash"] != frozen_input.context_hash
                or value["permissionScope"]["toolPolicy"] != frozen_input.tool_policy
                or value["principalRef"]
                != principal_reference(c["owner_internal_user_id"])
            ):
                raise BindingRejected()
            return result
        if (
            a["transport_run_id"] is not None
            or a["status"] != "running"
            or t["status"] not in {"accepted", "running"}
            or a["cancel_requested_at"] is not None
            or a["attempt_no"] != 1
            or c["updated_at"] != frozen_input.conversation_updated_at
            or t["updated_at"] != frozen_input.turn_updated_at
            or c["summary_through_seq"] != frozen_input.summary_through_seq
        ):
            raise BindingRejected()
        occupied = connection.execute(
            "select 1 from platform_control.direct_command_bindings "
            "where conversation_id=%s and accepted_at is null and retired_unsent_at is null",
            (c["conversation_id"],),
        ).fetchone()
        if occupied:
            raise BindingRejected()
        sequence = connection.execute(
            "select coalesce(max(command_seq),0)+1 as next from platform_control.direct_command_bindings "
            "where conversation_id=%s and accepted_at is not null",
            (c["conversation_id"],),
        ).fetchone()["next"]
        turn_sequence = connection.execute(
            "select count(*) as n from platform_control.conversation_turns t "
            "join platform_control.conversation_messages m on m.message_id=t.user_message_id "
            "where t.conversation_id=%s and m.seq<=%s",
            (c["conversation_id"], t["user_seq"]),
        ).fetchone()["n"]
        command_id, run_id = uuid4(), uuid4()
        principal = principal_reference(c["owner_internal_user_id"])
        frozen = parse_frozen_command(
            {
                "contractVersion": "core_chat_collaboration_v5",
                "runId": str(run_id),
                "commandId": str(command_id),
                "attemptId": str(lease.attempt_id),
                "attemptNo": a["attempt_no"],
                "turnId": str(t["turn_id"]),
                "turnSeq": turn_sequence,
                "commandSeq": sequence,
                "conversationId": str(c["conversation_id"]),
                "triggerMessageId": str(t["user_message_id"]),
                "principalRef": principal,
                "targetBot": "hr-bot",
                "prompt": frozen_input.prompt,
                "contextMode": "frozen_prompt",
                "contextHash": frozen_input.context_hash,
                "taskSessionId": f"platform:{c['conversation_id']}:hr-bot",
                "resultMode": "public_markdown",
                "permissionScope": {
                    "principalRef": principal,
                    "conversationId": str(c["conversation_id"]),
                    "agentId": "hr-bot",
                    "toolPolicy": frozen_input.tool_policy,
                },
                "retryOf": None,
                "inputAttachments": [],
                "outputScope": None,
            }
        )
        job_id = self.relay.enqueue_v5_template(frozen, connection=connection)
        connection.execute(
            "insert into platform_control.direct_command_bindings "
            "(attempt_id,command_id,job_id,conversation_id,command_seq,command_hash) values (%s,%s,%s,%s,%s,%s)",
            (
                lease.attempt_id,
                command_id,
                job_id,
                c["conversation_id"],
                sequence,
                frozen.command_hash,
            ),
        )
        connection.execute(
            "update platform_control.turn_attempts set transport_run_id=%s where attempt_id=%s",
            (run_id, lease.attempt_id),
        )
        return CommandBinding(
            lease.attempt_id, command_id, run_id, job_id, sequence, frozen
        )

    def retire_unoffered(self, lease, *, connection):
        self._lock(lease, connection)
        row = self._transport_row(lease, connection)
        if (
            row is None
            or row["offered_at"] is not None
            or row["accepted_at"] is not None
            or row["retired_unsent_at"] is not None
            or row["status"] != "queued"
            or row["lease_worker_id"] is not None
            or row["lease_expires_at"] is not None
        ):
            return False
        connection.execute(
            "update platform_control.execution_jobs set status='cancelled',cancel_requested=true,"
            "terminal_at=clock_timestamp(),updated_at=clock_timestamp() where job_id=%s",
            (row["job_id"],),
        )
        connection.execute(
            "update platform_control.direct_command_bindings set retired_unsent_at=clock_timestamp() "
            "where attempt_id=%s",
            (lease.attempt_id,),
        )
        return True

    def mark_offered(self, lease, *, connection):
        _, t, a = self._lock(lease, connection)
        row = self._transport_row(lease, connection)
        if (
            row is None
            or row["retired_unsent_at"] is not None
            or row["status"] != "queued"
            or row["cancel_requested"]
            or a["cancel_requested_at"] is not None
            or t["status"] not in {"accepted", "running"}
        ):
            return False
        connection.execute(
            "update platform_control.direct_command_bindings set offered_at=coalesce(offered_at,clock_timestamp()) "
            "where attempt_id=%s",
            (lease.attempt_id,),
        )
        return True

    def record_acceptance(self, lease, acceptance, *, connection):
        """Trusted adapter only: caller already validated authenticated HTTP acceptance."""
        self._lock(lease, connection)
        row = self._transport_row(lease, connection)
        if (
            not isinstance(acceptance, AcceptanceV5)
            or row is None
            or row["retired_unsent_at"] is not None
            or row["offered_at"] is None
            or acceptance.command_id != str(row["command_id"])
            or acceptance.run_id != str(row["run_id"])
            or acceptance.command_hash != row["command_hash"]
            or acceptance.transport_lease_epoch != lease.lease_epoch
            or (
                row["launch_lease_epoch"] is not None
                and row["launch_lease_epoch"] != acceptance.launch_lease_epoch
            )
        ):
            raise BindingRejected()
        if (
            row["accepted_at"] is not None
            and row["launch_lease_epoch"] == acceptance.launch_lease_epoch
        ):
            return False
        connection.execute(
            "update platform_control.direct_command_bindings set accepted_at=coalesce(accepted_at,clock_timestamp()),"
            "launch_lease_epoch=%s where attempt_id=%s",
            (acceptance.launch_lease_epoch, lease.attempt_id),
        )
        return True

    @staticmethod
    def _transport_row(lease, connection):
        binding = connection.execute(
            "select * from platform_control.direct_command_bindings where attempt_id=%s for update",
            (lease.attempt_id,),
        ).fetchone()
        if binding is None:
            return None
        job = connection.execute(
            "select * from platform_control.execution_jobs where job_id=%s and job_kind='worker_direct_v5' for update",
            (binding["job_id"],),
        ).fetchone()
        if job is None:
            raise BindingRejected()
        # A job lock can wait beyond the lease checked under C -> T -> A.
        # Recheck the DB clock only after the final required lock is held.
        current = connection.execute(
            "select 1 from platform_control.turn_attempts where attempt_id=%s "
            "and executor_id=%s and lease_epoch=%s and lease_expires_at>clock_timestamp()",
            (lease.attempt_id, str(lease.executor_id), lease.lease_epoch),
        ).fetchone()
        if current is None:
            raise LeaseRejected("attempt lease rejected")
        return {**job, **binding}

    def _decode(self, row):
        wrapper = self.relay.content_codec.unseal_json(
            f"execution-job:{row['job_id']}:{row['run_id']}",
            SealedContent(
                bytes(row["payload_ciphertext"]), row["encryption_key_version"]
            ),
        )
        if (
            type(wrapper) is not dict
            or set(wrapper) != {"format", "commandHash", "command"}
            or wrapper["format"] != "hr_frozen_command_v1"
        ):
            raise BindingRejected()
        frozen = parse_frozen_command(wrapper["command"])
        value = frozen.document
        if (
            wrapper["commandHash"] != row["command_hash"]
            or frozen.command_hash != row["command_hash"]
            or value["commandId"] != str(row["command_id"])
            or value["runId"] != str(row["run_id"])
            or value["attemptId"] != str(row["attempt_id"])
            or value["commandSeq"] != row["command_seq"]
        ):
            raise BindingRejected()
        return CommandBinding(
            row["attempt_id"],
            row["command_id"],
            row["run_id"],
            row["job_id"],
            row["command_seq"],
            frozen,
        )

    @staticmethod
    def _lock(lease, connection):
        if connection.autocommit:
            raise BindingRejected()
        c = connection.execute(
            "select c.* from platform_control.conversations c where conversation_id=("
            "select t.conversation_id from platform_control.conversation_turns t "
            "join platform_control.turn_attempts a using(turn_id) where a.attempt_id=%s) for update",
            (lease.attempt_id,),
        ).fetchone()
        if c is None:
            raise LeaseRejected("attempt lease rejected")
        t = connection.execute(
            "select t.*,m.seq as user_seq from platform_control.conversation_turns t "
            "join platform_control.conversation_messages m on m.message_id=t.user_message_id "
            "where t.turn_id=(select turn_id from platform_control.turn_attempts where attempt_id=%s) for update of t",
            (lease.attempt_id,),
        ).fetchone()
        a = connection.execute(
            "select * from platform_control.turn_attempts where attempt_id=%s for update",
            (lease.attempt_id,),
        ).fetchone()
        now = connection.execute("select clock_timestamp() as now").fetchone()["now"]
        if (
            a is None
            or t is None
            or c["status"] != "active"
            or c["mode"] != "direct_agent"
            or c["direct_agent_id"] != "hr-bot"
            or c["execution_owner"] != "worker_direct"
            or t["execution_owner"] != "worker_direct"
            or t["origin_route_epoch"] != c["route_epoch"]
            or a["executor_kind"] != "worker_direct"
            or lease.executor_kind != "worker_direct"
            or a["executor_id"] != str(lease.executor_id)
            or a["lease_epoch"] != lease.lease_epoch
            or a["lease_expires_at"] is None
            or a["lease_expires_at"] <= now
            or a["status"] not in {"running", "reconciling"}
        ):
            raise LeaseRejected("attempt lease rejected")
        return c, t, a
