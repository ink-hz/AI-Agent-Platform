from __future__ import annotations

from uuid import UUID

from .models import BindPositionConversation


class HrPositionScope:
    def __init__(self, repository) -> None:
        required = (
            "bind_conversation", "position_for_conversation",
            "attach_conversation_to_draft", "link_artifact",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("HR position scope repository required")
        self._repository = repository

    def for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> UUID | None:
        return self._repository.position_for_conversation(owner_id, conversation_id)

    def bind_conversation(
        self,
        owner_id: UUID,
        position_id: UUID,
        conversation_id: UUID,
        request_id: UUID,
    ):
        return self._repository.bind_conversation(BindPositionConversation(
            owner_id=owner_id,
            position_id=position_id,
            conversation_id=conversation_id,
            client_request_id=request_id,
            binding_kind="created_in_position",
        ))

    def bind_new_conversation_locked(
        self,
        cursor,
        owner_id: UUID,
        conversation_id: UUID,
        request_id: UUID,
        *,
        position_id: UUID | None = None,
        draft_id: UUID | None = None,
    ) -> None:
        if (position_id is None) == (draft_id is None):
            raise ValueError("exactly one HR position scope required")
        if position_id is not None:
            cursor.execute(
                "select (platform_hr.bind_conversation_v65("
                "%s,%s,%s,%s,'created_in_position')).*",
                (owner_id, position_id, conversation_id, request_id),
            ).fetchone()
            return
        cursor.execute(
            "select (platform_hr.attach_conversation_to_draft_v65("
            "%s,%s,%s,%s)).*",
            (owner_id, draft_id, conversation_id, request_id),
        ).fetchone()

    def attach_draft_conversation(
        self,
        owner_id: UUID,
        draft_id: UUID,
        conversation_id: UUID,
        request_id: UUID,
    ):
        return self._repository.attach_conversation_to_draft(
            owner_id, draft_id, conversation_id, request_id
        )

    def link_artifact(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        artifact_id: UUID,
    ) -> bool:
        position_id = self.for_conversation(owner_id, conversation_id)
        if position_id is None:
            return False
        self._repository.link_artifact(
            owner_id, position_id, artifact_id, artifact_id
        )
        return True
