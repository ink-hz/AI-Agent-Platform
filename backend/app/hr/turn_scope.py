"""The single owner/turn/position authorization boundary for HR v6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from app.execution_relay.contracts_v6 import HrTurnScope, HrMethodSelection
from app.execution_relay.contracts_v7 import HrResultRef


@dataclass(frozen=True)
class AuthorizedHrTurnScope:
    owner_id: UUID
    conversation_id: UUID
    turn_id: UUID
    scope: HrTurnScope
    method_selection: HrMethodSelection | None
    input_result_refs: tuple[HrResultRef,...] | None = None


def submission_context(submission) -> dict[str, Any]:
    scope = submission.hr_scope or HrTurnScope(
        positionId=None, positionCandidateIds=(), attachmentIds=submission.active_attachment_ids,
    )
    return {**({"inputResultRefs":[r.model_dump(mode="json",by_alias=True) for r in submission.input_result_refs]} if submission.input_result_refs is not None else {}), "scope": scope.model_dump(mode="json", by_alias=True),
            "methodSelection": submission.method_selection.model_dump(mode="json", by_alias=True)
            if submission.method_selection is not None else None}


def record_turn_scope_locked(cursor, owner_id: UUID, conversation_id: UUID,
                             turn_id: UUID, submission) -> None:
    cursor.execute("select platform_hr.record_turn_scope_v6(%s,%s,%s,%s)",
                   (owner_id, conversation_id, turn_id, Jsonb(submission_context(submission))))


def load_authorized_turn_scope(owner_id: UUID, conversation_id: UUID, turn_id: UUID,
                               *, connection) -> AuthorizedHrTurnScope:
    """Caller uses its authorized app/worker connection; no model-supplied owner."""
    if any(type(value) is not UUID for value in (owner_id, conversation_id, turn_id)):
        raise ValueError("HR turn identifiers invalid")
    row = connection.execute(
        "select platform_hr.read_turn_scope_v6(%s,%s,%s) as context",
        (owner_id, conversation_id, turn_id),
    ).fetchone()
    import json
    value = row["context"] if isinstance(row, dict) else row[0]
    scope = HrTurnScope.model_validate_json(json.dumps(value["scope"]))
    method = value["methodSelection"]
    return AuthorizedHrTurnScope(owner_id, conversation_id, turn_id, scope,
        HrMethodSelection.model_validate_json(json.dumps(method)) if method is not None else None,
        tuple(HrResultRef.model_validate_json(json.dumps(r)) for r in value["inputResultRefs"]) if "inputResultRefs" in value else None)
