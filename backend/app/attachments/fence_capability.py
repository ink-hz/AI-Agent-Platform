from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from app.control_plane.dsn import validate_control_dsn

ATTACHMENT_FENCE_MIGRATION_SHA256 = (
    "d0405bdb767d2cc74efae79b566fab9380432d277c57d59589569a5e31687170"
)
ATTACHMENT_FENCE_MIGRATION_VERSION = 107
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
                (ATTACHMENT_FENCE_MIGRATION_VERSION,),
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
            columns = connection.execute(
                "select attname,format_type(atttypid,atttypmod),attnotnull "
                "from pg_attribute where "
                "attrelid='platform_attachments.erasure_jobs'::regclass "
                "and attname in ('attempt_token','lease_expires_at','max_attempts') "
                "and attnum>0 and not attisdropped order by attname"
            ).fetchall()
            if columns != [
                ("attempt_token", "uuid", True),
                ("lease_expires_at", "timestamp with time zone", False),
                ("max_attempts", "integer", True),
            ]:
                raise AttachmentFenceCapabilityError()
            function_grants = connection.execute(
                "select "
                "has_function_privilege(%s,'platform_attachments."
                "claim_attachment_erasure_job_v107(text)','EXECUTE'),"
                "has_function_privilege(%s,'platform_attachments."
                "renew_attachment_erasure_lease_v107(uuid,uuid)','EXECUTE'),"
                "has_function_privilege(%s,'platform_attachments."
                "record_attachment_erasure_result_v107(uuid,uuid,text,text,jsonb)','EXECUTE'),"
                "has_function_privilege(%s,'platform_attachments."
                "recover_attachment_erasure_job_v107(uuid,uuid,integer)','EXECUTE'),"
                "has_function_privilege(%s,'platform_attachments."
                "claim_attachment_erasure_job_v64(text)','EXECUTE'),"
                "has_function_privilege(%s,'platform_attachments."
                "record_attachment_erasure_result_v64(uuid,text,text,jsonb)','EXECUTE')",
                (maintenance_role,) * 6,
            ).fetchone()
            if function_grants != (True, True, True, True, False, False):
                raise AttachmentFenceCapabilityError()
    except AttachmentFenceCapabilityError:
        raise
    except Exception:  # noqa: BLE001 - database clients are an opaque boundary
        raise AttachmentFenceCapabilityError() from None
