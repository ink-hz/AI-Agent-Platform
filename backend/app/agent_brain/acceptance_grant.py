from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from uuid import UUID

import psycopg

from app.execution_relay.register_worker import _secret_file, _secure_text_file


REFERENCE = "AGENT_BRAIN_ACCEPTANCE_001"


def _identity(value: object, *, role: str) -> UUID:
    if isinstance(value, str):
        return UUID(value)
    account_fields = {
        "internal_user_id",
        "display_name",
        "role",
        "departments",
        "observation_agent_ids",
        "directory_freshness",
        "hard_stale_read_only",
        "csrf_token",
    }
    if not isinstance(value, dict) or set(value) not in (
        {"internal_user_id", "role"},
        account_fields,
    ) or value.get("role") != role or not isinstance(
        value.get("internal_user_id"), str
    ):
        raise ValueError
    return UUID(value["internal_user_id"])


@dataclass(frozen=True)
class AcceptanceGrantInput:
    actor_internal_user_id: UUID
    member_internal_user_id: UUID
    grant_id: UUID
    request_id: UUID

    @classmethod
    def from_document(cls, document: object) -> "AcceptanceGrantInput":
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "actor",
            "member",
            "grant_id",
            "request_id",
        } or document["schema_version"] != 1:
            raise ValueError
        return cls(
            actor_internal_user_id=_identity(document["actor"], role="platform_owner"),
            member_internal_user_id=_identity(document["member"], role="member"),
            grant_id=UUID(document["grant_id"]),
            request_id=UUID(document["request_id"]),
        )

    @classmethod
    def from_file(cls, path: str) -> "AcceptanceGrantInput":
        return cls.from_document(json.loads(_secure_text_file(path, maximum_size=16_384)))


class AcceptanceGrantRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def apply(self, value: AcceptanceGrantInput) -> dict[str, bool]:
        row = self.connection.execute(
            "select hr_allowed,marketing_gtm_denied from "
            "platform_control.grant_agent_brain_acceptance_v33(%s,%s,%s,%s,%s)",
            (
                value.grant_id,
                value.member_internal_user_id,
                value.actor_internal_user_id,
                REFERENCE,
                value.request_id,
            ),
        ).fetchone()
        result = {
            "hr-bot": bool(row and row[0] is True),
            "marketing-gtm-bot": not bool(row and row[1] is True),
        }
        if result != {"hr-bot": True, "marketing-gtm-bot": False}:
            raise RuntimeError("Acceptance grant verification failed")
        return result


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    try:
        if len(values) != 1:
            raise ValueError
        value = AcceptanceGrantInput.from_file(values[0])
        with psycopg.connect(_secret_file()) as connection:
            result = AcceptanceGrantRepository(connection).apply(value)
        print("ACCEPTANCE_GRANT_OK hr-bot=allowed marketing-gtm-bot=denied")
        return 0
    except Exception:
        print("ACCEPTANCE_GRANT_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
