"""Owner-scoped repeatable-read snapshot; no orchestration or recovery writes."""

from app.attachments.result_artifact_recovery import artifact_enrichment

from .conversation_repository import ConversationRepositoryNotFound


class TurnSnapshotReader:
    def __init__(self, repository):
        self.repository = repository

    def get(self, owner_id, conversation_id, turn_id=None):
        with self.repository._connection() as connection:
            connection.execute(
                "set transaction isolation level repeatable read read only"
            )
            conversation = connection.execute(
                "select * from platform_control.conversations where conversation_id=%s and owner_internal_user_id=%s and mode='direct_agent' and direct_agent_id='hr-bot' and execution_owner='worker_direct'",
                (conversation_id, owner_id),
            ).fetchone()
            if conversation is None:
                raise ConversationRepositoryNotFound()
            cursor = connection.execute(
                "select coalesce(max(seq),0) as seq from platform_control.conversation_events where conversation_id=%s",
                (conversation_id,),
            ).fetchone()["seq"]
            result = {
                "read_version": conversation["snapshot_version"],
                "event_cursor": cursor,
                "turn": None,
                "attempt": None,
                "outcome": None,
                "answer": None,
                "result_enrichment": {
                    "status": "none",
                    "pending_count": 0,
                    "failed_count": 0,
                },
                "deliveries": [],
                "context_manifest_ref": None,
            }
            turn = connection.execute(
                "select t.*,m.seq as user_seq,(select count(*) from platform_control.conversation_turns prior join platform_control.conversation_messages pm on pm.message_id=prior.user_message_id where prior.conversation_id=t.conversation_id and pm.seq<=m.seq) as turn_seq "
                "from platform_control.conversation_turns t join platform_control.conversation_messages m on m.message_id=t.user_message_id "
                "where t.conversation_id=%s and (%s::uuid is null or t.turn_id=%s) order by m.seq desc limit 1",
                (conversation_id, turn_id, turn_id),
            ).fetchone()
            if turn is None:
                if turn_id is not None:
                    raise ConversationRepositoryNotFound()
                return result
            attempt = connection.execute(
                "select * from platform_control.turn_attempts where turn_id=%s and executor_kind='worker_direct' order by attempt_no desc limit 1",
                (turn["turn_id"],),
            ).fetchone()
            if attempt is None:
                raise ConversationRepositoryNotFound()
            binding = connection.execute(
                "select * from platform_control.direct_command_bindings where attempt_id=%s",
                (attempt["attempt_id"],),
            ).fetchone()
            terminal = turn["status"] in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }
            status = turn["status"] if terminal else attempt["status"]
            result.update(
                turn={
                    "turn_id": str(turn["turn_id"]),
                    "turn_seq": turn["turn_seq"],
                    "status": status,
                },
                attempt={
                    "attempt_id": str(attempt["attempt_id"]),
                    "attempt_no": attempt["attempt_no"],
                    "lease_epoch": attempt["lease_epoch"],
                    "status": attempt["status"],
                    "reason_code": attempt["reason_code"],
                },
                context_manifest_ref=f"context-manifest:command:{binding['command_id']}"
                if binding
                else f"context-manifest:intake:{turn['turn_id']}",
            )
            if terminal:
                result["outcome"] = {
                    "terminal": True,
                    "kind": status,
                    "reason_code": None
                    if status == "completed"
                    else attempt["reason_code"],
                }
            if status == "completed":
                message = connection.execute(
                    "select * from platform_control.conversation_messages where message_id=%s and conversation_id=%s and turn_id=%s and role='assistant' and delivery_status='completed'",
                    (turn["assistant_message_id"], conversation_id, turn["turn_id"]),
                ).fetchone()
                if (
                    message is None
                    or binding is None
                    or binding["published_message_id"] != message["message_id"]
                ):
                    raise ConversationRepositoryNotFound()
                record = self.repository._message_from_row(message)
                result["answer"] = {
                    "message_id": str(record.message_id),
                    "role": "assistant",
                    "content": record.content,
                    "completed_at": record.completed_at.isoformat(),
                }
                result["result_enrichment"] = artifact_enrichment(
                    connection, record.message_id
                )
            return result
