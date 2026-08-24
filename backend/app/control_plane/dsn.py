from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict


_DATABASE_ENVIRONMENTS = {
    "agent_platform_control": "production",
    "agent_platform_control_preview": "preview",
}
_PURPOSE_ROLES = {
    "app": "platform_control_app",
    "audit": "platform_audit_append",
    "migrator": "platform_control_migrator",
    "maintenance": "platform_control_maintenance",
    "directory": "platform_directory_worker",
    "stream": "platform_stream_ingest",
    "brain": "platform_brain_worker",
}


@dataclass(frozen=True)
class ControlDsn:
    database: str
    user: str
    environment: str
    purpose: str


def validate_control_dsn(dsn: str, *, purpose: str) -> ControlDsn:
    base_role = _PURPOSE_ROLES.get(purpose)
    if base_role is None:
        raise ValueError("control DSN purpose invalid")
    message = f"exact control {purpose} DSN required"
    try:
        values = conninfo_to_dict(dsn)
    except (TypeError, ValueError, psycopg.Error):
        raise ValueError(message) from None
    database = values.get("dbname")
    user = values.get("user")
    environment = _DATABASE_ENVIRONMENTS.get(database or "")
    expected_user = base_role + ("_preview" if environment == "preview" else "")
    if environment is None or not user or user != expected_user:
        raise ValueError(message)
    return ControlDsn(
        database=database,
        user=user,
        environment=environment,
        purpose=purpose,
    )
