"""Existing Mission compatibility over the single durable v5 Attempt/binding."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.attachments.grant_service import TaskGrantError
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.contracts_v5 import parse_v5_event
from app.execution_relay.models import (
    OutputWriteGrantPayload,
    TaskAttachmentGrantPayload,
)

from .conversation_context import MAX_CONTEXT_BYTES, ConversationContextError, ConversationContextTooLarge
from .direct_command_binding import BindingRejected, FrozenInput
from .repository import _run_subject, _task_subject
from .turn_attempts import TerminalEvidence


class DirectMissionAdapter:
    def __init__(self, attempts, bindings, context_builder, projector, *, attachment_grants=None):
        self.attempts, self.bindings = attempts, bindings
        self.context_builder, self.projector = context_builder, projector
        self.attachment_grants = attachment_grants

    def prepare(self, lease):
        with self.attempts.transaction() as connection:
            conversation, turn, attempt = self.bindings._lock(lease, connection)
            prepared = self.bindings.get_prepared(lease, connection=connection)
            if prepared is not None:
                return prepared
            if attempt["cancel_requested_at"] is not None:
                return self._unstarted(
                    connection,
                    lease,
                    conversation,
                    turn,
                    "cancelled",
                    "cancelled_before_dispatch",
                )
        context = self.context_builder.build_direct(
            conversation["conversation_id"], turn["turn_id"]
        )
        selected = list(context.active_attachment_ids)
        if context.hr_position_context is not None:
            selected.extend(context.hr_position_context.material_attachment_ids)
            selected.extend(context.hr_position_context.document_attachment_ids)
        selected = tuple(dict.fromkeys(selected))
        if selected and self.attachment_grants is None:
            raise ConversationContextError("material_execution_unavailable")
        if len(selected) > 5:
            raise ConversationContextError("material_execution_unavailable")
        document = {
            "summary": context.summary,
            "messages": [asdict(m) for m in context.messages],
            "hr_workflow_contract": context.hr_workflow_contract,
            "hr_position_context": context.hr_position_context.prompt_context
            if context.hr_position_context
            else None,
            "hr_panorama_context": context.hr_panorama_context.as_prompt_document()
            if context.hr_panorama_context
            else None,
        }
        if context.hr_reference_knowledge is not None:
            document["hr_reference_knowledge"] = context.hr_reference_knowledge
        prompt = json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(prompt.encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise ConversationContextTooLarge()
        admission = lease.admission
        if not admission:
            raise BindingRejected()
        frozen = FrozenInput(
            conversation["owner_internal_user_id"],
            conversation["updated_at"],
            turn["updated_at"],
            conversation["summary_through_seq"],
            turn["user_message_id"],
            turn["user_seq"],
            prompt,
            hashlib.sha256(prompt.encode()).hexdigest(),
            admission["service"]["config"]["toolPolicy"],
        )
        with self.attempts.transaction() as connection:
            self.bindings._lock(lease, connection)
            prepared = self.bindings.get_prepared(lease, connection=connection)
            if prepared is not None:
                return prepared
            authorized = connection.execute(
                "select allowed from platform_control.resolve_agent_use_decision_v41(%s,'hr-bot')",
                (conversation["owner_internal_user_id"],),
            ).fetchone()
            if not authorized or not authorized["allowed"]:
                return self._unstarted(
                    connection, lease, conversation, turn, "failed", "agent_use_denied"
                )
            # The compatibility task, its existing grants and the frozen command
            # commit together; a lost commit response cannot issue a second set.
            run_id = uuid4()
            self._compatibility(connection, run_id, turn, prompt)
            inputs, output = [], None
            if self.attachment_grants is not None:
                expires_at = datetime.now(UTC) + timedelta(hours=24)
                try:
                    inputs = [TaskAttachmentGrantPayload.model_validate(asdict(
                        self.attachment_grants.issue_attachment(
                            run_id, attachment_id, "hr-bot", expires_at=expires_at,
                            connection=connection,
                        )
                    )).model_dump(mode="json", by_alias=True) for attachment_id in selected]
                    if sum(value["sizeBytes"] for value in inputs) > 50 * 1024 * 1024:
                        raise ConversationContextError("material_execution_unavailable")
                    output = OutputWriteGrantPayload.model_validate(asdict(
                        self.attachment_grants.issue_output(
                            run_id, "hr-bot", expires_at=expires_at, connection=connection,
                        )
                    )).model_dump(mode="json", by_alias=True)
                except TaskGrantError:
                    raise ConversationContextError("material_execution_unavailable") from None
            frozen = replace(frozen, run_id=run_id, input_grants=tuple(inputs), output_grant=output)
            binding = self.bindings.prepare(lease, frozen, connection=connection)
            self.bindings.authorize_transport(
                lease,
                admission["workerId"],
                admission["callbackOrigin"],
                connection=connection,
            )
            self.attempts.assert_current(lease, connection=connection)
            return binding

    def _compatibility(self, connection, run_id, turn, prompt):
        codec, mission_id = self.bindings.relay.content_codec, turn["mission_id"]
        objective = codec.seal_json(
            _task_subject(mission_id, run_id), {"text": prompt}
        )
        payload = codec.seal_json(
            _run_subject(mission_id, run_id, "input"), {"text": prompt}
        )
        connection.execute(
            "insert into platform_control.mission_tasks(task_id,mission_id,agent_id,objective_ciphertext,encryption_key_version,status,started_at) values(%s,%s,'hr-bot',%s,%s,'running',clock_timestamp()) on conflict(task_id) do nothing",
            (run_id, mission_id, objective.ciphertext, objective.key_version),
        )
        connection.execute(
            "insert into platform_control.mission_runs(run_id,mission_id,task_id,phase,agent_id,status,input_ciphertext,encryption_key_version,started_at) values(%s,%s,%s,'direct','hr-bot','running',%s,%s,clock_timestamp()) on conflict(run_id) do nothing",
            (
                run_id,
                mission_id,
                run_id,
                payload.ciphertext,
                payload.key_version,
            ),
        )
        connection.execute(
            "update platform_control.missions set status='delegated',updated_at=clock_timestamp(),row_version=row_version+1 where mission_id=%s",
            (mission_id,),
        )

    def _unstarted(self, connection, lease, conversation, turn, status, reason):
        row = self.bindings._transport_row(lease, connection)
        if row and (row["offered_at"] is not None or row["accepted_at"] is not None):
            raise BindingRejected()
        if row:
            self.bindings.retire_unoffered(lease, connection=connection)
        self._terminal_turn(connection, conversation, turn, status)
        self.attempts.record_terminal(
            lease, TerminalEvidence(status, reason_code=reason), connection=connection
        )

    def _terminal_turn(self, connection, conversation, turn, status):
        connection.execute(
            "update platform_control.conversation_turns set status=%s,updated_at=clock_timestamp() where turn_id=%s",
            (status, turn["turn_id"]),
        )
        connection.execute(
            "update platform_control.missions set status=%s,terminal_at=clock_timestamp(),updated_at=clock_timestamp(),row_version=row_version+1 where mission_id=%s",
            (status, turn["mission_id"]),
        )
        connection.execute(
            "update platform_control.mission_tasks set status=%s,terminal_at=clock_timestamp(),updated_at=clock_timestamp() where mission_id=%s",
            (status, turn["mission_id"]),
        )
        connection.execute(
            "update platform_control.mission_runs set status=%s,terminal_at=clock_timestamp(),updated_at=clock_timestamp() where mission_id=%s",
            (status, turn["mission_id"]),
        )
        self.projector._event(
            connection,
            conversation["conversation_id"],
            turn["turn_id"],
            turn["mission_id"],
            f"turn.{status}",
            {"status": status},
        )
        connection.execute(
            "update platform_control.conversations set snapshot_version=snapshot_version+1,updated_at=clock_timestamp() where conversation_id=%s",
            (conversation["conversation_id"],),
        )

    def reconcile(self, lease):
        with self.attempts.transaction() as connection:
            conversation, turn, attempt = self.bindings._lock(lease, connection)
            row = self.bindings._transport_row(lease, connection)
            if row is None or row["offered_at"] is None:
                if attempt["status"] == "reconciling" or attempt["cancel_requested_at"]:
                    return self._unstarted(
                        connection,
                        lease,
                        conversation,
                        turn,
                        "cancelled"
                        if attempt["cancel_requested_at"]
                        else "interrupted",
                        "never_dispatched",
                    )
                return None
            self._progress(connection, lease, conversation, turn, row)
            source = connection.execute(
                "select * from platform_control.v5_source_events where run_id=%s and event_type in ('result','error','cancelled','interrupted') limit 1",
                (row["run_id"],),
            ).fetchone()
            if source:
                raw = self.bindings.relay.content_codec.unseal_json(
                    f"execution-v5-source:{row['run_id']}:{source['seq']}",
                    SealedContent(
                        bytes(source["payload_ciphertext"]),
                        source["encryption_key_version"],
                    ),
                )
                event = parse_v5_event(json.loads(raw["raw"]))
                if event.event_type == "result":
                    message_id = self.projector.commit_locked(connection, lease, event)
                    if (
                        row["executor_stop_proof_ref"]
                        and not event.payload.execution_recovery.executor_stopped
                    ):
                        self.projector._event(
                            connection,
                            conversation["conversation_id"],
                            turn["turn_id"],
                            turn["mission_id"],
                            "turn.completed",
                            {"status": "completed", "message_id": str(message_id)},
                        )
                        connection.execute(
                            "update platform_control.conversations set snapshot_version=snapshot_version+1,updated_at=clock_timestamp() where conversation_id=%s",
                            (conversation["conversation_id"],),
                        )
                        self.attempts.record_terminal(
                            lease,
                            TerminalEvidence("completed", message_id),
                            connection=connection,
                        )
                    return message_id
                if row["executor_stop_proof_ref"]:
                    status = (
                        "cancelled"
                        if attempt["cancel_requested_at"]
                        else event.payload.terminal
                    )
                    self._terminal_turn(connection, conversation, turn, status)
                    self.attempts.record_terminal(
                        lease,
                        TerminalEvidence(status, reason_code="executor_stopped"),
                        connection=connection,
                    )
                    return None
            now = connection.execute("select clock_timestamp() as now").fetchone()[
                "now"
            ]
            if (
                source
                or attempt["cancel_requested_at"]
                or attempt["status"] == "reconciling"
                or (
                    row["accepted_at"] is None
                    and row["offered_at"] < now - timedelta(seconds=10)
                )
            ) and (
                attempt["status"] != "reconciling"
                or attempt["reason_code"] != "executor_stop_unknown"
            ):
                connection.execute(
                    "update platform_control.turn_attempts set status='reconciling',reason_code='executor_stop_unknown',updated_at=clock_timestamp() where attempt_id=%s",
                    (lease.attempt_id,),
                )
                connection.execute(
                    "update platform_control.conversations set snapshot_version=snapshot_version+1 where conversation_id=%s",
                    (conversation["conversation_id"],),
                )
            self.attempts.assert_current(lease, connection=connection)
            return None

    def _progress(self, connection, lease, conversation, turn, binding):
        sources = connection.execute(
            "select * from platform_control.v5_source_events where run_id=%s and seq>%s "
            "and event_type in ('raw_progress','run_heartbeat') order by seq limit 100",
            (binding["run_id"], binding["progress_source_seq"]),
        ).fetchall()
        visible = False
        for source in sources:
            stored = self.bindings.relay.content_codec.unseal_json(
                f"execution-v5-source:{binding['run_id']}:{source['seq']}",
                SealedContent(
                    bytes(source["payload_ciphertext"]),
                    source["encryption_key_version"],
                ),
            )
            event = parse_v5_event(json.loads(stored["raw"]))
            kind, summary = None, None
            if event.event_type == "run_heartbeat":
                kind, summary = "agent.task_progress", "HR Agent 仍在处理"
            else:
                # Only known public-text origins may cross the private source
                # boundary. Never expose thinking, logs, tokens or source refs.
                kind = {
                    ("provider", "agent_message"): "agent.message",
                    ("agent_sdk", "work_update"): "agent.work_update",
                }.get((event.payload.source, event.payload.kind))
                if kind:
                    summary = event.payload.text
            if kind:
                self.projector._event(
                    connection,
                    conversation["conversation_id"],
                    turn["turn_id"],
                    turn["mission_id"],
                    kind,
                    {"summary": summary},
                )
                visible = True
        if sources:
            connection.execute(
                "update platform_control.direct_command_bindings set progress_source_seq=%s where attempt_id=%s",
                (sources[-1]["seq"], lease.attempt_id),
            )
        if visible:
            connection.execute(
                "update platform_control.conversations set snapshot_version=snapshot_version+1 where conversation_id=%s",
                (conversation["conversation_id"],),
            )
        self.attempts.assert_current(lease, connection=connection)

    def failed_prepare(self, lease, reason):
        with self.attempts.transaction() as connection:
            conversation, turn, attempt = self.bindings._lock(lease, connection)
            cancelled = attempt["cancel_requested_at"] is not None
            return self._unstarted(
                connection,
                lease,
                conversation,
                turn,
                "cancelled" if cancelled else "failed",
                "cancelled_before_dispatch" if cancelled else reason,
            )
