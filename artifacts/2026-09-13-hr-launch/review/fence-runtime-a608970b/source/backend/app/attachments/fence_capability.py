from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from app.control_plane.dsn import validate_control_dsn

ATTACHMENT_FENCE_MIGRATION_SHA256 = (
    "7c4ae854dccb96292d33ed66af2070d8f555fcdf5539150fd7a592fedcd0cabb"
)
_PURPOSE_ROLES = {
    "app": "platform_control_app",
    "brain": "platform_brain_worker",
    "maintenance": "platform_control_maintenance",
}


class AttachmentFenceCapabilityError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment fence capability unavailable")


def require_attachment_fence_capability(
    database_url: str,
    *,
    purpose: str,
    connect: Callable[..., Any] = psycopg.connect,
) -> None:
    """Verify the exact database capability needed before attachment writes."""
    try:
        parsed = validate_control_dsn(database_url, purpose=purpose)
        base_role = _PURPOSE_ROLES[purpose]
        suffix = "_preview" if parsed.environment == "preview" else ""
        expected_role = base_role + suffix
        maintenance_role = "platform_control_maintenance" + suffix
        with connect(
            database_url,
            connect_timeout=3,
            options="-c statement_timeout=3000 -c timezone=UTC",
        ) as connection:
            identity = connection.execute(
                "select session_user,current_user,current_database()"
            ).fetchone()
            if identity != (expected_role, expected_role, parsed.database):
                raise AttachmentFenceCapabilityError()
            ledger = connection.execute(
                "select sha256 from platform_control.schema_migrations "
                "where version=%s",
                (106,),
            ).fetchone()
            if ledger != (ATTACHMENT_FENCE_MIGRATION_SHA256,):
                raise AttachmentFenceCapabilityError()
            grants = connection.execute(
                "select "
                "has_column_privilege(%s,'platform_attachments.processing_jobs',"
                "'attachment_id','SELECT'),"
                "has_column_privilege(%s,'platform_attachments.processing_jobs',"
                "'processing_job_id','SELECT'),"
                "has_column_privilege(%s,'platform_attachments.processing_jobs',"
                "'job_kind','SELECT'),"
                "has_column_privilege(%s,'platform_attachments.processing_jobs',"
                "'derivative_kind','SELECT')",
                (maintenance_role,) * 4,
            ).fetchone()
            if grants != (True, True, True, True):
                raise AttachmentFenceCapabilityError()
    except AttachmentFenceCapabilityError:
        raise
    except Exception:  # noqa: BLE001 - database clients are an opaque boundary
        raise AttachmentFenceCapabilityError() from None
