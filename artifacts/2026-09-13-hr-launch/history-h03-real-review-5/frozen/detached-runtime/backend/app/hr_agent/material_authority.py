"""Prior verified content identity plus current metadata; never a fresh byte audit."""

from uuid import UUID

from psycopg.errors import ForeignKeyViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .types import HrAgentProblem, content_sha256, problem, validate_contract


def source_identity_sha(row):
    # 097 also keys the parse lookup by source SHA before checking its identity.
    # Include that SHA here: the identity digest must stand alone in this table.
    identity = {
        name: str(row[name])
        for name in (
            "immutable_locator",
            "write_attempt_id",
            "detected_mime",
            "size_bytes",
        )
    }
    identity["source_sha256"] = bytes(row["sha256"]).hex()
    return bytes.fromhex(content_sha256(identity))


def record_verified(connection_factory, owner_id, row, ref):
    """Internal receipt writer, called only after MaterialService verifies text."""
    try:
        with connection_factory() as connection:
            connection.execute(
                "INSERT INTO platform_hr_agent.material_authority_proofs"
                "(owner_id,attachment_id,text_revision,text_sha256,source_identity_sha)"
                " VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    owner_id,
                    row["attachment_id"],
                    ref["revision"],
                    bytes.fromhex(ref["sha256"]),
                    source_identity_sha(row),
                ),
            )
    except ForeignKeyViolation:
        raise problem("reference_unavailable", http_status=410) from None
    except Exception:  # noqa: BLE001 - database internals are not material metadata
        raise problem(
            "temporarily_unavailable", retryable=True, http_status=503
        ) from None


def authorize_refs(connection_factory, owner_id, refs):
    if not isinstance(owner_id, UUID):
        raise problem("scope_denied", http_status=401)
    requested = {}
    for ref in refs:
        ref = validate_contract("ExactRef", ref)
        if ref["kind"] != "material":
            raise problem("invalid_input")
        try:
            attachment_id = UUID(ref["id"].split(":", 1)[0])
        except ValueError:
            raise problem("reference_unavailable", http_status=410) from None
        if ref["id"] != f"{attachment_id}:text":
            raise problem("reference_unavailable", http_status=410)
        key = (attachment_id, ref["revision"], ref["sha256"])
        requested[key] = {
            "attachment_id": str(attachment_id),
            "text_revision": ref["revision"],
            "text_sha256": ref["sha256"],
        }
    if not requested:
        return
    try:
        with (
            connection_factory() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            cursor.execute(
                """
                WITH requested AS (
                  SELECT * FROM jsonb_to_recordset(%s) AS r(
                    attachment_id uuid,text_revision text,text_sha256 text)
                )
                SELECT r.attachment_id,r.text_revision,r.text_sha256,
                  a.state,a.sha256,a.detected_mime,a.size_bytes,a.immutable_locator,
                  a.retained_until>statement_timestamp() AS retained,
                  u.write_attempt_id,p.source_identity_sha,
                  EXISTS(SELECT 1 FROM platform_attachments.erasure_jobs e
                         WHERE e.attachment_id=a.attachment_id) AS erasing
                FROM requested r
                LEFT JOIN platform_attachments.attachments a
                  ON a.attachment_id=r.attachment_id AND a.owner_internal_user_id=%s
                LEFT JOIN platform_attachments.uploads u
                  ON u.attachment_id=a.attachment_id
                LEFT JOIN platform_hr_agent.material_authority_proofs p
                  ON p.attachment_id=a.attachment_id AND p.owner_id=a.owner_internal_user_id
                  AND p.text_revision=r.text_revision
                  AND p.text_sha256=decode(r.text_sha256,'hex')
                """,
                (Jsonb(list(requested.values())), owner_id),
            )
            allowed = set()
            for row in cursor:
                if (
                    row["state"] == "ready"
                    and row["retained"]
                    and not row["erasing"]
                    and row["sha256"]
                    and row["immutable_locator"]
                    and row["source_identity_sha"] is not None
                    and bytes(row["source_identity_sha"]) == source_identity_sha(row)
                ):
                    allowed.add(
                        (row["attachment_id"], row["text_revision"], row["text_sha256"])
                    )
        if allowed != set(requested):
            raise problem("reference_unavailable", http_status=410)
    except HrAgentProblem:
        raise
    except Exception:  # noqa: BLE001 - no private storage or database diagnostics
        raise problem(
            "temporarily_unavailable", retryable=True, http_status=503
        ) from None
