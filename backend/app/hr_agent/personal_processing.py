"""Outbound approval for known personal sources, including derived results."""

from uuid import UUID

from .types import canonical_json, problem


def authorize_personal_processing(repository, cursor, owner, refs):
    pending, seen, attachments = list(refs), set(), set()
    while pending:
        ref = pending.pop()
        identity = canonical_json(ref)
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > 10000:
            raise problem("temporarily_unavailable", http_status=503)
        if ref["kind"] == "material":
            attachment = UUID(ref["id"].split(":", 1)[0])
            cursor.execute(
                "SELECT 1 FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
                (owner, attachment),
            )
            if cursor.fetchone():
                attachments.add(attachment)
        cursor.execute(
            "SELECT source_kind AS kind,source_id AS id,source_revision AS revision,source_sha256 AS sha256 FROM platform_hr_agent.reference_edges WHERE owner_id=%s AND dependent_kind=%s AND dependent_id=%s AND dependent_revision=%s",
            (owner, ref["kind"], ref["id"], ref["revision"]),
        )
        pending.extend(cursor.fetchall())
    authorizer = getattr(repository, "personal_processing_authorizer", None)
    for attachment in attachments:
        try:
            allowed = authorizer is not None and authorizer(owner, attachment) is True
        except Exception:  # noqa: BLE001 - authority failure must deny outbound processing
            allowed = False
        if not allowed:
            raise problem(
                "configuration_unavailable",
                details={"field": "personal_processing_authorization"},
                http_status=503,
            )
