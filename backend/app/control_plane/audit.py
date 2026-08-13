from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, TypeVar
from uuid import UUID, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


_AUDIT_EVENT_NAMESPACE = UUID("8fabf404-553e-4e15-bdd8-c744c05e1f5a")
_ALLOWED_METADATA = frozenset(
    {
        "agent_id",
        "approver_a",
        "approver_b",
        "directory_generation_id",
        "linked_audit_event_id",
        "new_role",
        "operation",
        "os_operator",
        "previous_role",
        "result",
        "role",
        "session_revocation_count",
    }
)
_RESULTS = frozenset({"requested", "completed", "failed"})
T = TypeVar("T")


class AuditUnavailableError(RuntimeError):
    """A required immutable audit event could not be persisted."""


class IndeterminateMutationError(AuditUnavailableError):
    """The mutation committed, but its immutable outcome is not confirmed."""

    def __init__(self, request_id: UUID, requested_audit_event_id: UUID) -> None:
        super().__init__("management mutation outcome indeterminate")
        self.request_id = request_id
        self.requested_audit_event_id = requested_audit_event_id


@dataclass(frozen=True)
class AuditCommand:
    event_type: str
    actor_internal_user_id: UUID
    target_type: str
    target_id: str
    request_id: UUID
    reason: str
    metadata: Mapping[str, str | int | bool]


def sanitize_governance_metadata(
    metadata: Mapping[str, str | int | bool],
) -> dict[str, str | int | bool]:
    if not isinstance(metadata, Mapping):
        raise ValueError("audit metadata invalid")
    sanitized: dict[str, str | int | bool] = {}
    for key in sorted(metadata):
        value = metadata[key]
        if key not in _ALLOWED_METADATA:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, str) and value and len(value) <= 256 and "\0" not in value:
            sanitized[key] = value
    return sanitized


def _event_result(event_type: str, metadata: Mapping[str, Any]) -> str:
    supplied = metadata.get("result")
    if supplied in _RESULTS:
        return str(supplied)
    for result in _RESULTS:
        if event_type.endswith(f"_{result}"):
            return result
    return "completed"


def _event_id(command: AuditCommand) -> UUID:
    identity = json.dumps(
        {
            "actor": str(command.actor_internal_user_id),
            "event": command.event_type,
            "request": str(command.request_id),
            "target_id": command.target_id,
            "target_type": command.target_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return uuid5(_AUDIT_EVENT_NAMESPACE, identity)


class AuditRepository:
    _CONTROL_DATABASES = {
        "agent_platform_control",
        "agent_platform_control_preview",
    }

    def __init__(self, audit_database_url: str, *, connect=psycopg.connect) -> None:
        try:
            database_name = conninfo_to_dict(audit_database_url).get("dbname")
        except (TypeError, ValueError, psycopg.Error):
            raise ValueError("audit database DSN required") from None
        if database_name not in self._CONTROL_DATABASES:
            raise ValueError("audit database DSN required")
        self._database_url = audit_database_url
        self._connect = connect

    def append(
        self,
        event_id: UUID,
        command: AuditCommand,
        sanitized: Mapping[str, str | int | bool],
    ) -> UUID:
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select platform_control.append_audit_event("
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) as event_id",
                    (
                        event_id,
                        command.actor_internal_user_id,
                        command.event_type,
                        command.target_type,
                        command.target_id,
                        command.request_id,
                        _event_result(command.event_type, sanitized),
                        command.reason.strip(),
                        json.dumps(sanitized, sort_keys=True),
                    ),
                ).fetchone()
                if row is None or row["event_id"] != event_id:
                    raise AuditUnavailableError("required audit unavailable")
                return event_id
        except AuditUnavailableError:
            raise
        except psycopg.Error:
            raise AuditUnavailableError("required audit unavailable") from None


class AuditWriter:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    @classmethod
    def from_database_url(cls, audit_database_url: str) -> AuditWriter:
        return cls(AuditRepository(audit_database_url))

    def append(self, command: AuditCommand) -> UUID:
        if (
            not isinstance(command, AuditCommand)
            or not isinstance(command.reason, str)
            or not command.reason.strip()
        ):
            raise ValueError("audit reason required")
        if (
            not command.event_type
            or not command.target_type
            or not command.target_id
            or not isinstance(command.actor_internal_user_id, UUID)
            or not isinstance(command.request_id, UUID)
        ):
            raise ValueError("audit command invalid")
        sanitized = sanitize_governance_metadata(command.metadata)
        event_id = _event_id(command)
        return self.repository.append(event_id, command, sanitized)


def _outcome_command(
    requested: AuditCommand,
    requested_audit_event_id: UUID,
    result: str,
) -> AuditCommand:
    suffix = "_requested"
    base = (
        requested.event_type[: -len(suffix)]
        if requested.event_type.endswith(suffix)
        else requested.event_type
    )
    metadata = dict(requested.metadata)
    metadata.update(
        {
            "linked_audit_event_id": str(requested_audit_event_id),
            "result": result,
        }
    )
    return AuditCommand(
        event_type=f"{base}_{result}",
        actor_internal_user_id=requested.actor_internal_user_id,
        target_type=requested.target_type,
        target_id=requested.target_id,
        request_id=requested.request_id,
        reason=requested.reason,
        metadata=metadata,
    )


class SensitiveMutationCoordinator:
    """Coordinate independent immutable-audit and control-db commits.

    The audit and application DSNs are separate connections. The immutable
    requested event commits first. The control mutation then stores that event
    ID. A missing outcome is reported as indeterminate and can be reconciled by
    retrying the same request; event IDs and linked mutations are idempotent.
    """

    def __init__(self, audit_writer: Any) -> None:
        self.audit_writer = audit_writer

    def execute(
        self,
        *,
        requested: AuditCommand,
        mutate: Callable[[UUID], T],
    ) -> T:
        requested_event_id = self.audit_writer.append(requested)
        try:
            result = mutate(requested_event_id)
        except Exception:
            try:
                self.audit_writer.append(
                    _outcome_command(requested, requested_event_id, "failed")
                )
            except AuditUnavailableError:
                pass
            raise
        try:
            self.audit_writer.append(
                _outcome_command(requested, requested_event_id, "completed")
            )
        except AuditUnavailableError:
            raise IndeterminateMutationError(
                requested.request_id, requested_event_id
            ) from None
        return result
