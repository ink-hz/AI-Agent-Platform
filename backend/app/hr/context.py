from __future__ import annotations

from uuid import UUID



class HrPositionScope:
    def __init__(self, repository) -> None:
        required = (
            "position_for_conversation", "link_artifact",
        )
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise ValueError("HR position scope repository required")
        self._repository = repository

    def for_conversation(
        self, owner_id: UUID, conversation_id: UUID, *, turn_id: UUID
    ) -> UUID | None:
        return self._repository.position_for_conversation(owner_id, conversation_id, turn_id=turn_id)

    def link_artifact(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        artifact_id: UUID,
        *, turn_id: UUID,
    ) -> bool:
        position_id = self.for_conversation(owner_id, conversation_id, turn_id=turn_id)
        if position_id is None:
            return False
        self._repository.link_artifact(
            owner_id, position_id, artifact_id, artifact_id
        )
        return True
