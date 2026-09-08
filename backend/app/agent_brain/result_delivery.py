"""Durable, independently retryable enrichment of an already committed answer."""
from __future__ import annotations

import logging
from uuid import UUID

from app.agent_brain.conversation_repository import ConversationRepository

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


class ConversationResultDelivery:
    def __init__(self, repository: ConversationRepository, result_projection: object) -> None:
        self.repository = repository
        self.result_projection = result_projection

    def _project_one(self, mission_id: UUID) -> bool:
        with self.repository._connection() as connection, connection.cursor() as cursor:
            row = cursor.execute(
                "select delivery.mission_id,delivery.message_id,delivery.attempts,"
                "message.conversation_id,conversation.owner_internal_user_id "
                "from platform_control.conversation_result_deliveries delivery "
                "join platform_control.conversation_messages message "
                "on message.message_id=delivery.message_id and message.mission_id=delivery.mission_id "
                "join platform_control.conversations conversation using(conversation_id) "
                "where delivery.mission_id=%s and delivery.status='pending' "
                "and delivery.next_attempt_at<=now() "
                "and message.role='assistant' and message.delivery_status='completed' "
                "for update of delivery skip locked", (mission_id,),
            ).fetchone()
            if row is None:
                return False
            attempt = row["attempts"] + 1
            try:
                # The savepoint rolls back partial citation/binding writes;
                # the outer transaction persists failure and backoff.
                with connection.transaction():
                    delivery = self.repository._missions.terminal_delivery_for_projection(cursor, mission_id)
                    if (delivery.event_type != "mission.completed" or delivery.task_id is None
                            or delivery.agent_id is None or delivery.output_payload is None):
                        raise ValueError("result delivery invalid")
                    self.result_projection.project_locked(
                        cursor, owner_id=row["owner_internal_user_id"],
                        conversation_id=row["conversation_id"], message_id=row["message_id"],
                        task_id=delivery.task_id, agent_id=delivery.agent_id,
                        collaboration=delivery.output_payload.get("collaboration"),
                    )
            except Exception:
                cursor.execute(
                    "update platform_control.conversation_result_deliveries "
                    "set status=%s,attempts=%s,next_attempt_at=now()+(%s * interval '1 second'),"
                    "last_error_code='result_projection_unavailable',updated_at=now() where mission_id=%s",
                    ("failed" if attempt >= MAX_ATTEMPTS else "pending", attempt,
                     min(300, 5 * 2 ** (attempt - 1)), mission_id),
                )
                logger.warning("Conversation result enrichment unavailable",
                               extra={"mission_id": str(mission_id), "attempt": attempt})
                return False
            cursor.execute(
                "update platform_control.conversation_result_deliveries "
                "set status='completed',attempts=%s,last_error_code=null,updated_at=now() where mission_id=%s",
                (attempt, mission_id),
            )
            return True

    def project_pending(self, *, limit: int = 50) -> int:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Result delivery limit invalid")
        with self.repository._connection() as connection, connection.cursor() as cursor:
            mission_ids = [row["mission_id"] for row in cursor.execute(
                "select mission_id from platform_control.conversation_result_deliveries "
                "where status='pending' and next_attempt_at<=now() "
                "order by next_attempt_at,mission_id limit %s", (limit,),
            ).fetchall()]
        completed = 0
        for mission_id in mission_ids:
            try:
                completed += self._project_one(mission_id)
            except Exception:
                # Connection/commit failure leaves the row due for another pass.
                logger.warning("Conversation result delivery transaction unavailable",
                               extra={"mission_id": str(mission_id)})
        return completed
