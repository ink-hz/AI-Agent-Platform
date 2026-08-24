from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.agent_brain.conversation_models import ConversationCreateResult


@dataclass(frozen=True, slots=True)
class ConversationCancelResult:
    conversation_id: UUID
    turn_id: UUID
    mission_id: UUID | None
    cancel_requested: bool


class ConversationCommandService:
    """Select the retained direct-Agent path or the durable Brain command path."""

    def __init__(
        self,
        repository,
        *,
        v2_enabled: bool,
        model_config: dict[str, object] | None = None,
        max_steps: int = 12,
        max_tasks: int = 8,
        max_duration_seconds: int = 900,
    ) -> None:
        if type(v2_enabled) is not bool:
            raise ValueError("V2 Conversation flag invalid")
        self._repository = repository
        self.v2_enabled = v2_enabled
        self._model_config = dict(
            model_config
            or {
                "config_version": "brain-opus5-v1",
                "model_id": "claude-opus-5",
            }
        )
        self._max_steps = max_steps
        self._max_tasks = max_tasks
        self._max_duration_seconds = max_duration_seconds

    def start(
        self,
        owner: UUID,
        request_id: UUID,
        text: str,
        *,
        mode: Literal["brain", "direct_agent"] = "brain",
        direct_agent_id: str | None = None,
    ) -> ConversationCreateResult:
        if not self.v2_enabled or mode == "direct_agent":
            return self._repository.start(
                owner,
                request_id,
                text,
                mode=mode,
                direct_agent_id=direct_agent_id,
            )
        return self._repository.start_v2(
            owner,
            request_id,
            text,
            model_config=self._model_config,
            max_steps=self._max_steps,
            max_tasks=self._max_tasks,
            max_duration_seconds=self._max_duration_seconds,
        )

    def append_turn(
        self,
        owner: UUID,
        conversation_id: UUID,
        request_id: UUID,
        text: str,
    ) -> ConversationCreateResult:
        conversation = self._repository.conversation_for_owner(
            owner, conversation_id
        )
        if not self.v2_enabled or conversation.mode == "direct_agent":
            return self._repository.append_turn(
                owner, conversation_id, request_id, text
            )
        return self._repository.append_turn_v2(
            owner,
            conversation_id,
            request_id,
            text,
            model_config=self._model_config,
            max_steps=self._max_steps,
            max_tasks=self._max_tasks,
            max_duration_seconds=self._max_duration_seconds,
        )

    def retry_turn(
        self,
        owner: UUID,
        conversation_id: UUID,
        failed_turn_id: UUID,
        request_id: UUID,
    ) -> ConversationCreateResult:
        if not self.v2_enabled:
            raise ValueError("V2 Conversation retry disabled")
        return self._repository.retry_turn_v2(
            owner,
            conversation_id,
            failed_turn_id,
            request_id,
            model_config=self._model_config,
            max_steps=self._max_steps,
            max_tasks=self._max_tasks,
            max_duration_seconds=self._max_duration_seconds,
        )

    def resume_waiting_user(
        self,
        owner: UUID,
        conversation_id: UUID,
        request_id: UUID,
        text: str,
    ) -> ConversationCreateResult:
        return self._repository.resume_waiting_user_v2(
            owner, conversation_id, request_id, text
        )

    def request_cancel(
        self, owner: UUID, conversation_id: UUID
    ) -> ConversationCancelResult:
        conversation = self._repository.conversation_for_owner(
            owner, conversation_id
        )
        if not self.v2_enabled or conversation.mode == "direct_agent":
            mission = self._repository.request_cancel(owner, conversation_id)
            return ConversationCancelResult(
                conversation_id,
                mission.turn_id,
                mission.mission_id,
                mission.cancel_requested,
            )
        turn = self._repository.request_cancel_v2(owner, conversation_id)
        return ConversationCancelResult(
            conversation_id,
            turn.turn_id,
            None,
            True,
        )
