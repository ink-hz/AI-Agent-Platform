from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.conversation_repository import (
    ConversationRepositoryConflict,
    ConversationRepositoryError,
    ConversationRepositoryNotFound,
)

from .position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceUnavailable,
)
from .repository import HrNotFound, HrUnavailable

TaskStatus = Literal["accepted", "running", "completed", "failed"]
POSITION_TASK_KINDS = frozenset(
    {
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "position_interview_plan",
    }
)
CANDIDATE_TASK_KINDS = frozenset({"candidate_match", "candidate_interview_plan"})
STARTABLE_TASK_KINDS = POSITION_TASK_KINDS | CANDIDATE_TASK_KINDS

_PROMPTS = {
    "jd": "基于当前已确认的岗位上下文生成岗位说明（JD）。",
    "jr": "基于当前已确认的岗位上下文生成岗位要求（JR）。",
    "talent_profile": "基于当前已确认的岗位上下文生成人才画像。",
    "sourcing_strategy": "基于当前已确认的岗位上下文生成候选人搜寻策略。",
    "position_interview_plan": "基于当前已确认的岗位上下文生成岗位面试方案。",
    "candidate_match": "基于当前岗位上下文和候选人材料生成匹配分析。",
    "candidate_interview_plan": "基于当前岗位上下文和候选人材料生成专属面试题。",
}


class HrPositionTaskError(RuntimeError):
    pass


class HrPositionTaskNotFound(HrPositionTaskError):
    pass


class HrPositionTaskConflict(HrPositionTaskError):
    pass


class HrPositionTaskUnavailable(HrPositionTaskError):
    pass


@dataclass(frozen=True, slots=True)
class HrPositionTask:
    task_id: UUID
    task_kind: str
    status: TaskStatus
    error: str | None
    conversation_id: UUID | None
    turn_id: UUID | None
    candidate_id: UUID | None
    position_candidate_id: UUID | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_id, UUID)
            or self.task_kind not in STARTABLE_TASK_KINDS
        ):
            raise ValueError("HR position task projection invalid")
        if self.status not in {"accepted", "running", "completed", "failed"}:
            raise ValueError("HR position task projection invalid")
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error
        ):
            raise ValueError("HR position task projection invalid")
        for value in (
            self.conversation_id,
            self.turn_id,
            self.candidate_id,
            self.position_candidate_id,
        ):
            if value is not None and not isinstance(value, UUID):
                raise ValueError("HR position task projection invalid")
        if (self.conversation_id is None) != (self.turn_id is None):
            raise ValueError("HR position task projection invalid")
        if (self.candidate_id is None) != (self.position_candidate_id is None):
            raise ValueError("HR position task projection invalid")


class TaskProjectionRepository(Protocol):
    def position_exists(self, owner_id: UUID, position_id: UUID) -> bool: ...
    def recoverable_tasks(
        self, owner_id: UUID, position_id: UUID
    ) -> tuple[HrPositionTask, ...]: ...
    def task(
        self, owner_id: UUID, position_id: UUID, task_id: UUID
    ) -> HrPositionTask | None: ...


def _canonical_payload(
    *,
    position_id: UUID,
    task_kind: str,
    context_version_id: UUID | None,
    material_ids: tuple[UUID, ...],
    conversation_id: UUID | None,
    candidate_id: UUID | None,
    position_candidate_id: UUID | None,
) -> str:
    value = {
        "candidate_id": str(candidate_id) if candidate_id else None,
        "context_version_id": str(context_version_id) if context_version_id else None,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "material_ids": [str(item) for item in material_ids],
        "position_candidate_id": (
            str(position_candidate_id) if position_candidate_id else None
        ),
        "position_id": str(position_id),
        "task_kind": task_kind,
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status(value: object) -> TaskStatus:
    if value == "accepted":
        return "accepted"
    if value in {"running", "waiting_agents", "waiting_user", "completing"}:
        return "running"
    if value == "completed":
        return "completed"
    if value in {"failed", "cancelled", "interrupted"}:
        return "failed"
    raise HrPositionTaskUnavailable("conversation task status unavailable")


class HrPositionTaskService:
    def __init__(self, intelligence, conversations, position_scope, projection) -> None:
        if not callable(getattr(intelligence, "create_task_request", None)):
            raise TypeError("position intelligence service required")
        if any(
            not callable(getattr(conversations, name, None))
            for name in ("start", "append_turn")
        ):
            raise ValueError("conversation command service required")
        if any(
            not callable(getattr(position_scope, name, None))
            for name in ("for_conversation", "bind_new_conversation_locked")
        ):
            raise ValueError("HR position scope required")
        if any(
            not callable(getattr(projection, name, None))
            for name in ("position_exists", "recoverable_tasks", "task")
        ):
            raise ValueError("HR position task projection required")
        self._intelligence = intelligence
        self._conversations = conversations
        self.position_scope = position_scope
        self._projection = projection

    def start(
        self,
        *,
        owner_id: UUID,
        position_id: UUID,
        request_id: UUID,
        task_kind: str,
        context_version_id: UUID | None,
        material_ids: tuple[UUID, ...],
        conversation_id: UUID | None,
        candidate_id: UUID | None,
        position_candidate_id: UUID | None,
    ) -> HrPositionTask:
        if any(
            not isinstance(value, UUID) for value in (owner_id, position_id, request_id)
        ):
            raise ValueError("HR position task identifiers invalid")
        for value in (
            context_version_id,
            conversation_id,
            candidate_id,
            position_candidate_id,
        ):
            if value is not None and not isinstance(value, UUID):
                raise ValueError("HR position task identifiers invalid")
        if (
            task_kind not in STARTABLE_TASK_KINDS
            or not isinstance(material_ids, tuple)
            or len(material_ids) > 100
            or len(set(material_ids)) != len(material_ids)
            or any(not isinstance(value, UUID) for value in material_ids)
        ):
            raise ValueError("HR position task request invalid")
        candidate_pair = candidate_id is not None and position_candidate_id is not None
        if (
            (candidate_id is None) != (position_candidate_id is None)
            or (
                task_kind in CANDIDATE_TASK_KINDS
                and (not candidate_pair or context_version_id is None)
            )
            or (task_kind in POSITION_TASK_KINDS and candidate_pair)
        ):
            raise ValueError("HR position task candidate selection invalid")
        normalized_materials = tuple(sorted(material_ids, key=str))
        payload_hash = _canonical_payload(
            position_id=position_id,
            task_kind=task_kind,
            context_version_id=context_version_id,
            material_ids=normalized_materials,
            conversation_id=conversation_id,
            candidate_id=candidate_id,
            position_candidate_id=position_candidate_id,
        )
        try:
            if (
                conversation_id is not None
                and self.position_scope.for_conversation(owner_id, conversation_id)
                != position_id
            ):
                raise HrPositionTaskNotFound("position conversation not found")
            request = self._intelligence.create_task_request(
                owner_id=owner_id,
                position_id=position_id,
                request_id=request_id,
                canonical_payload_sha256=payload_hash,
                task_kind=task_kind,
                expected_context_version_id=context_version_id,
                material_attachment_ids=normalized_materials,
                candidate_id=candidate_id,
                position_candidate_id=position_candidate_id,
            )
            # Position materials remain pinned to the HR request; the HR context
            # grants them without rebinding their Conversation ownership.
            submission = ConversationTurnSubmission(_PROMPTS[task_kind])
            if conversation_id is None:
                result = self._conversations.start(
                    owner_id,
                    request_id,
                    submission,
                    mode="direct_agent",
                    direct_agent_id="hr-bot",
                    hr_position_scope=self.position_scope,
                    position_id=position_id,
                )
            else:
                result = self._conversations.append_turn(
                    owner_id,
                    conversation_id,
                    request_id,
                    submission,
                )
            projected_conversation_id = result.conversation.conversation_id
            turn_id = result.turn.turn_id
            if not isinstance(request.task_request_id, UUID):
                raise HrPositionTaskUnavailable("position task request unavailable")
            return HrPositionTask(
                request.task_request_id,
                task_kind,
                _status(result.turn.status),
                None,
                projected_conversation_id,
                turn_id,
                candidate_id,
                position_candidate_id,
            )
        except HrPositionTaskError:
            raise
        except PositionContextNotFound:
            raise HrPositionTaskNotFound("position task not found") from None
        except PositionContextConflict:
            raise HrPositionTaskConflict("position task conflict") from None
        except PositionIntelligenceUnavailable:
            raise HrPositionTaskUnavailable("position task unavailable") from None
        except ConversationRepositoryNotFound:
            raise HrPositionTaskNotFound("position conversation not found") from None
        except ConversationRepositoryConflict:
            raise HrPositionTaskConflict("position conversation conflict") from None
        except ConversationRepositoryError:
            raise HrPositionTaskUnavailable("conversation task unavailable") from None
        except HrNotFound:
            raise HrPositionTaskNotFound("position conversation not found") from None
        except HrUnavailable:
            raise HrPositionTaskUnavailable("position task unavailable") from None
        except (AttributeError, TypeError):
            raise HrPositionTaskUnavailable("position task unavailable") from None

    def recoverable(
        self, owner_id: UUID, position_id: UUID
    ) -> tuple[HrPositionTask, ...]:
        if not isinstance(owner_id, UUID) or not isinstance(position_id, UUID):
            raise TypeError("HR position task identifiers invalid")
        try:
            if not self._projection.position_exists(owner_id, position_id):
                raise HrPositionTaskNotFound("position not found")
            return self._projection.recoverable_tasks(owner_id, position_id)
        except HrPositionTaskError:
            raise
        except Exception as error:
            raise HrPositionTaskUnavailable("position tasks unavailable") from error

    def get(
        self, owner_id: UUID, position_id: UUID, task_id: UUID
    ) -> HrPositionTask:
        if any(not isinstance(value, UUID) for value in (owner_id, position_id, task_id)):
            raise TypeError("HR position task identifiers invalid")
        try:
            task = self._projection.task(owner_id, position_id, task_id)
            if task is None:
                raise HrPositionTaskNotFound("position task not found")
            return task
        except HrPositionTaskError:
            raise
        except Exception as error:
            raise HrPositionTaskUnavailable("position task unavailable") from error
