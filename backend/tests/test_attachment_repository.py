from datetime import datetime, timezone
from uuid import UUID

from app.attachments.repository import AttachmentRepository


ATTACHMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class FakeDatabase:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def execute(self, statement, params):
        self.executed.append((" ".join(statement.split()), params))
        return self

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_issue_ticket_uses_database_function_and_caps_ttl() -> None:
    expires = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
    database = FakeDatabase(
        [
            {
                "result": {
                    "ticket": "opaque",
                    "expires_at": expires.isoformat(),
                    "content_path": "/api/attachments/content/opaque",
                }
            }
        ]
    )
    repository = AttachmentRepository("postgresql://unused", connect=database)

    issued = repository.issue_ticket(ATTACHMENT_ID, "download", 9999)

    statement, params = database.executed[0]
    assert "flywheel_api.issue_attachment_ticket(%s,%s,%s,%s)" in statement
    assert params == (ATTACHMENT_ID, "download", "platform-local", 300)
    assert issued.ticket == "opaque"
    assert issued.expires_at == expires


def test_unavailable_ticket_request_returns_none() -> None:
    database = FakeDatabase(
        [
            {
                "result": {
                    "status": "rejected",
                    "reason": "ATTACHMENT_UNAVAILABLE",
                }
            }
        ]
    )
    repository = AttachmentRepository("postgresql://unused", connect=database)

    assert repository.issue_ticket(ATTACHMENT_ID, "preview", 300) is None


def test_resolve_and_access_use_only_security_definer_functions() -> None:
    database = FakeDatabase(
        [
            {
                "attachment_id": ATTACHMENT_ID,
                "purpose": "download",
                "display_name": "report.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 12,
                "bucket": "orbbec-agent-attachments",
                "object_key": "sha256/aa/object",
                "sha256": "a" * 64,
            }
        ]
    )
    repository = AttachmentRepository("postgresql://unused", connect=database)

    resolved = repository.resolve_ticket(
        "opaque-ticket", {"request_id": "req-1", "range_requested": True}
    )
    repository.record_access(
        resolved, "streamed", {"request_id": "req-1", "ticket": "must-not-pass"}
    )

    resolve_sql, resolve_params = database.executed[0]
    audit_sql, audit_params = database.executed[1]
    assert "flywheel_api.resolve_attachment_ticket(%s,%s,%s::jsonb)" in resolve_sql
    assert resolve_params[0:2] == ("opaque-ticket", "platform-local")
    assert (
        "flywheel_api.record_attachment_access(%s,%s,%s,%s,%s::jsonb)"
        in audit_sql
    )
    assert audit_params[:4] == (
        ATTACHMENT_ID,
        "platform-local",
        "download",
        "streamed",
    )
    assert "ticket" not in audit_params[4]
    assert resolved.object_key == "sha256/aa/object"
