from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import event_subject, message_subject
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, ContentCryptoError


RETENTION_DAYS = 365
RETENTION_BATCH_LIMIT = 100
RETENTION_TOMBSTONE_TEXT = "[内容已按保留策略清除]"


class ConversationRetentionError(RuntimeError):
    """Stable retention failure without protected content or SQL details."""


class ConversationRetention:
    """Tombstone encrypted content for expired, archived Conversations.

    Identifiers, timestamps, source hashes and terminal state remain intact for
    audit.  The plaintext marker on the Conversation is the idempotent erasure
    boundary; archived content is never deleted or made restorable.
    """

    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="brain")
        if not isinstance(content_codec, ContentCodec):
            raise ValueError("content codec required")
        self._control_database_url = control_database_url
        self._content_codec = content_codec
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def erase_expired(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= RETENTION_BATCH_LIMIT:
            raise ValueError("Conversation retention limit invalid")
        try:
            with self._connection() as connection, connection.transaction():
                conversations = connection.execute(
                    "select conversation_id from platform_control.conversations "
                    "where status='archived' and archived_at is not null "
                    "and archived_at<=clock_timestamp()-interval '365 days' "
                    "and title<>%s order by archived_at,conversation_id "
                    "for update skip locked limit %s",
                    (RETENTION_TOMBSTONE_TEXT, limit),
                ).fetchall()
                for row in conversations:
                    self._erase_conversation(connection, row["conversation_id"])
            return len(conversations)
        except (ContentCryptoError, KeyError, TypeError, ValueError, psycopg.Error):
            raise ConversationRetentionError("Conversation retention unavailable") from None

    def _seal(self, subject: str, value: Mapping[str, object]):
        return self._content_codec.seal_json(subject, dict(value))

    def _erase_conversation(self, connection, conversation_id: UUID) -> None:
        for row in connection.execute(
            "select message_id from platform_control.conversation_messages "
            "where conversation_id=%s order by seq for update",
            (conversation_id,),
        ):
            sealed = self._seal(
                message_subject(conversation_id, row["message_id"]),
                {"text": RETENTION_TOMBSTONE_TEXT},
            )
            connection.execute(
                "update platform_control.conversation_messages set "
                "content_ciphertext=%s,encryption_key_version=%s where message_id=%s",
                (sealed.ciphertext, sealed.key_version, row["message_id"]),
            )

        for row in connection.execute(
            "select event_id from platform_control.conversation_events "
            "where conversation_id=%s order by seq for update",
            (conversation_id,),
        ):
            sealed = self._seal(
                event_subject(conversation_id, row["event_id"]),
                {"status": "retention_erased"},
            )
            connection.execute(
                "update platform_control.conversation_events set "
                "payload_ciphertext=%s,encryption_key_version=%s where event_id=%s",
                (sealed.ciphertext, sealed.key_version, row["event_id"]),
            )

        loops = connection.execute(
            "select loop_id from platform_brain.brain_loops "
            "where conversation_id=%s order by created_at,loop_id for update",
            (conversation_id,),
        ).fetchall()
        for loop in loops:
            self._erase_loop(connection, loop["loop_id"])

        connection.execute(
            "update platform_control.conversations set title=%s,"
            "summary_ciphertext=null,summary_key_version=null "
            "where conversation_id=%s",
            (RETENTION_TOMBSTONE_TEXT, conversation_id),
        )

    def _erase_loop(self, connection, loop_id: UUID) -> None:
        sealed_loop = self._seal(
            f"brain-loop:{loop_id}:model-config", {"retention_erased": True}
        )
        connection.execute(
            "update platform_brain.brain_loops set model_config_ciphertext=%s,"
            "model_config_key_version=%s where loop_id=%s",
            (sealed_loop.ciphertext, sealed_loop.key_version, loop_id),
        )

        steps = connection.execute(
            "select step_id from platform_brain.brain_steps where loop_id=%s "
            "order by step_seq for update",
            (loop_id,),
        ).fetchall()
        for step in steps:
            self._erase_step(connection, step["step_id"])

        tasks = connection.execute(
            "select task_id from platform_brain.agent_tasks where loop_id=%s "
            "order by created_at,task_id for update",
            (loop_id,),
        ).fetchall()
        for task in tasks:
            self._erase_task(connection, task["task_id"])

        for row in connection.execute(
            "select intervention_id from platform_brain.brain_user_interventions "
            "where loop_id=%s order by created_at,intervention_id for update",
            (loop_id,),
        ):
            sealed = self._seal(
                f"brain-intervention:{row['intervention_id']}",
                {"text": RETENTION_TOMBSTONE_TEXT},
            )
            connection.execute(
                "update platform_brain.brain_user_interventions set "
                "content_ciphertext=%s,content_key_version=%s where intervention_id=%s",
                (sealed.ciphertext, sealed.key_version, row["intervention_id"]),
            )

        for row in connection.execute(
            "select through_step_seq from platform_brain.brain_checkpoints "
            "where loop_id=%s order by through_step_seq for update",
            (loop_id,),
        ):
            sealed = self._seal(
                f"brain-loop:{loop_id}:checkpoint:{row['through_step_seq']}",
                {"retention_erased": True},
            )
            connection.execute(
                "update platform_brain.brain_checkpoints set "
                "checkpoint_ciphertext=%s,checkpoint_key_version=%s where "
                "loop_id=%s and through_step_seq=%s",
                (
                    sealed.ciphertext,
                    sealed.key_version,
                    loop_id,
                    row["through_step_seq"],
                ),
            )

    def _erase_step(self, connection, step_id: UUID) -> None:
        connection.execute(
            "update platform_brain.brain_steps set model_response_ciphertext=null,"
            "model_response_key_version=null,response_erased_at=coalesce("
            "response_erased_at,clock_timestamp()) "
            "where step_id=%s",
            (step_id,),
        )
        for row in connection.execute(
            "select brain_tool_call_id,result_ciphertext from "
            "platform_brain.brain_tool_calls where step_id=%s "
            "order by tool_index for update",
            (step_id,),
        ):
            tool_call_id = row["brain_tool_call_id"]
            arguments = self._seal(
                f"brain-tool-call:{tool_call_id}:arguments",
                {"retention_erased": True},
            )
            result = (
                self._seal(
                    f"brain-tool-call:{tool_call_id}:result",
                    {"retention_erased": True},
                )
                if row["result_ciphertext"] is not None
                else None
            )
            connection.execute(
                "update platform_brain.brain_tool_calls set "
                "arguments_ciphertext=%s,arguments_key_version=%s,"
                "result_ciphertext=%s,result_key_version=%s "
                "where brain_tool_call_id=%s",
                (
                    arguments.ciphertext,
                    arguments.key_version,
                    result.ciphertext if result else None,
                    result.key_version if result else None,
                    tool_call_id,
                ),
            )

        for row in connection.execute(
            "select block_index from platform_brain.brain_thinking_summaries "
            "where step_id=%s order by block_index for update",
            (step_id,),
        ):
            sealed = self._seal(
                f"brain-step:{step_id}:thinking:{row['block_index']}",
                {"text": RETENTION_TOMBSTONE_TEXT},
            )
            connection.execute(
                "update platform_brain.brain_thinking_summaries set "
                "summary_ciphertext=%s,summary_key_version=%s "
                "where step_id=%s and block_index=%s",
                (sealed.ciphertext, sealed.key_version, step_id, row["block_index"]),
            )

    def _erase_task(self, connection, task_id: UUID) -> None:
        context = self._seal(
            f"brain-task:{task_id}:context", {"retention_erased": True}
        )
        connection.execute(
            "update platform_brain.agent_tasks set task_context_ciphertext=%s,"
            "task_context_key_version=%s where task_id=%s",
            (context.ciphertext, context.key_version, task_id),
        )

        session = connection.execute(
            "select adapter_session_ref_ciphertext from "
            "platform_brain.agent_task_sessions where task_id=%s for update",
            (task_id,),
        ).fetchone()
        if session is not None:
            reference = (
                self._seal(
                    f"brain-task:{task_id}:session-ref",
                    {"retention_erased": True},
                )
                if session["adapter_session_ref_ciphertext"] is not None
                else None
            )
            connection.execute(
                "update platform_brain.agent_task_sessions set "
                "adapter_session_ref_ciphertext=%s,adapter_session_ref_key_version=%s,"
                "capability_snapshot='{}'::jsonb "
                "where task_id=%s",
                (
                    reference.ciphertext if reference else None,
                    reference.key_version if reference else None,
                    task_id,
                ),
            )

        for row in connection.execute(
            "select seq from platform_brain.agent_task_messages where task_id=%s "
            "order by seq for update",
            (task_id,),
        ):
            sealed = self._seal(
                f"brain-task:{task_id}:message:{row['seq']}",
                {"text": RETENTION_TOMBSTONE_TEXT},
            )
            connection.execute(
                "update platform_brain.agent_task_messages set "
                "content_ciphertext=%s,content_key_version=%s where task_id=%s and seq=%s",
                (sealed.ciphertext, sealed.key_version, task_id, row["seq"]),
            )

        for row in connection.execute(
            "select seq from platform_brain.agent_task_events where task_id=%s "
            "order by seq for update",
            (task_id,),
        ):
            sealed = self._seal(
                f"brain-task:{task_id}:event:{row['seq']}:payload",
                {"status": "retention_erased"},
            )
            connection.execute(
                "update platform_brain.agent_task_events set "
                "payload_ciphertext=%s,payload_key_version=%s where task_id=%s and seq=%s",
                (sealed.ciphertext, sealed.key_version, task_id, row["seq"]),
            )
