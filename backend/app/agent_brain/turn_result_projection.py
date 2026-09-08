"""Publish only immutable authenticated source under the current Attempt fence.

An answer is not proof the native executor stopped. Publication and occupancy
release are deliberately separate, while sharing the original command binding.
"""

import json
from uuid import uuid4

from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.contracts_v5 import CoreChatEventV5, parse_v5_event

from .conversation_repository import event_subject, message_subject
from .turn_attempts import TerminalEvidence


class TurnResultProjector:
    def __init__(self, attempts, bindings):
        self.attempts = attempts
        self.bindings = bindings
        self.codec = bindings.relay.content_codec

    def commit(self, lease, event):
        with self.attempts.transaction() as connection:
            return self.commit_locked(connection, lease, event)

    def commit_locked(self, connection, lease, event):
        if not isinstance(event, CoreChatEventV5):
            raise TypeError("authenticated source event required")
        conversation, turn, _ = self.bindings._lock(lease, connection)
        binding = self.bindings._transport_row(lease, connection)
        if binding is None or (
            event.run_id,
            event.command_id,
            event.attempt_id,
            event.lease_epoch,
        ) != (
            binding["run_id"],
            binding["command_id"],
            lease.attempt_id,
            binding["launch_lease_epoch"],
        ):
            raise ValueError("authenticated source identity mismatch")
        source = connection.execute(
            "select * from platform_control.v5_source_events where run_id=%s and seq=%s",
            (event.run_id, event.seq),
        ).fetchone()
        if source is None:
            raise ValueError("authenticated source required")
        payload = self.codec.unseal_json(
            f"execution-v5-source:{event.run_id}:{event.seq}",
            SealedContent(
                bytes(source["payload_ciphertext"]), source["encryption_key_version"]
            ),
        )
        persisted = parse_v5_event(json.loads(payload["raw"]))
        if persisted != event or source["event_type"] != event.event_type:
            raise ValueError("authenticated source mismatch")
        if event.event_type != "result":
            return None
        if binding["terminal_source_seq"] is not None:
            if binding["terminal_source_seq"] != event.seq:
                raise ValueError("authenticated source already decided")
            return binding["published_message_id"]
        if turn["assistant_message_id"] is not None:
            raise ValueError("authenticated source conflicts with published answer")
        conversation_id, turn_id = conversation["conversation_id"], turn["turn_id"]
        message_id = uuid4()
        sealed = self.codec.seal_json(
            message_subject(conversation_id, message_id),
            {"text": event.payload.public_answer_markdown},
        )
        connection.execute(
            "insert into platform_control.conversation_messages(message_id,conversation_id,seq,role,"
            "content_ciphertext,encryption_key_version,turn_id,mission_id,delivery_status,completed_at) "
            "select %s,%s,coalesce(max(seq),0)+1,'assistant',%s,%s,%s,%s,'completed',clock_timestamp() "
            "from platform_control.conversation_messages where conversation_id=%s",
            (
                message_id,
                conversation_id,
                sealed.ciphertext,
                sealed.key_version,
                turn_id,
                turn["mission_id"],
                conversation_id,
            ),
        )
        connection.execute(
            "update platform_control.conversation_turns set status='completed',assistant_message_id=%s,updated_at=clock_timestamp() where turn_id=%s",
            (message_id, turn_id),
        )
        connection.execute(
            "update platform_control.missions set status='completed',terminal_at=clock_timestamp(),updated_at=clock_timestamp(),row_version=row_version+1 where mission_id=%s",
            (turn["mission_id"],),
        )
        connection.execute(
            "update platform_control.mission_tasks set status='completed',terminal_at=clock_timestamp(),updated_at=clock_timestamp() where task_id=%s and mission_id=%s",
            (event.run_id, turn["mission_id"]),
        )
        connection.execute(
            "update platform_control.mission_runs set status='completed',terminal_at=clock_timestamp(),updated_at=clock_timestamp() where run_id=%s and mission_id=%s",
            (event.run_id, turn["mission_id"]),
        )
        connection.execute(
            "update platform_control.direct_command_bindings set terminal_source_seq=%s,published_message_id=%s where attempt_id=%s",
            (event.seq, message_id, lease.attempt_id),
        )
        self._event(
            connection,
            conversation_id,
            turn_id,
            turn["mission_id"],
            "message.completed",
            {"message_id": str(message_id)},
        )
        self._event(
            connection,
            conversation_id,
            turn_id,
            turn["mission_id"],
            "turn.completed",
            {"status": "completed", "message_id": str(message_id)},
        )
        connection.execute(
            "update platform_control.conversations set snapshot_version=snapshot_version+1,updated_at=clock_timestamp() where conversation_id=%s",
            (conversation_id,),
        )
        recovery = event.payload.execution_recovery
        if recovery.executor_stopped:
            connection.execute(
                "update platform_control.direct_command_bindings set executor_stop_proof_ref=%s where attempt_id=%s",
                (recovery.executor_stop_proof_ref, lease.attempt_id),
            )
            self.attempts.record_terminal(
                lease, TerminalEvidence("completed", message_id), connection=connection
            )
        else:
            connection.execute(
                "update platform_control.turn_attempts set status='reconciling',reason_code='executor_stop_unknown',updated_at=clock_timestamp() where attempt_id=%s",
                (lease.attempt_id,),
            )
        self.attempts.assert_current(lease, connection=connection)
        return message_id

    def _event(self, connection, conversation_id, turn_id, mission_id, kind, payload):
        event_id = uuid4()
        sealed = self.codec.seal_json(event_subject(conversation_id, event_id), payload)
        connection.execute(
            "insert into platform_control.conversation_events(event_id,conversation_id,seq,turn_id,mission_id,event_type,payload_ciphertext,encryption_key_version) "
            "select %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s,%s from platform_control.conversation_events where conversation_id=%s",
            (
                event_id,
                conversation_id,
                turn_id,
                mission_id,
                kind,
                sealed.ciphertext,
                sealed.key_version,
                conversation_id,
            ),
        )
