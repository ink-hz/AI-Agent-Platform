from collections.abc import Callable
from typing import Any
from uuid import UUID
import json

import psycopg
from psycopg.rows import dict_row

from .models import ResolvedAttachment, Ticket


ACTOR = "platform-local"
TRUSTED_CONTEXT_KEYS = ("request_id", "remote_class", "range_requested")


class AttachmentRepositoryError(RuntimeError):
    pass


def _context(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in TRUSTED_CONTEXT_KEYS
        if value.get(key) is not None
    }


class AttachmentRepository:
    def __init__(
        self, database_url: str, *, connect: Callable = psycopg.connect
    ) -> None:
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=5000",
            row_factory=dict_row,
        )

    def issue_ticket(
        self, attachment_id: UUID, purpose: str, ttl_seconds: int
    ) -> Ticket | None:
        try:
            with self._connection() as connection:
                row = connection.cursor().execute(
                    "select flywheel_api.issue_attachment_ticket"
                    "(%s,%s,%s,%s) as result",
                    (attachment_id, purpose, ACTOR, min(max(ttl_seconds, 1), 300)),
                ).fetchone()
            result = row["result"] if row else None
            if not result or result.get("ticket") is None:
                return None
            return Ticket.model_validate(result)
        except AttachmentRepositoryError:
            raise
        except Exception as error:
            raise AttachmentRepositoryError("attachment ticket query failed") from error

    def resolve_ticket(
        self, ticket: str, context: dict[str, Any]
    ) -> ResolvedAttachment | None:
        try:
            with self._connection() as connection:
                row = connection.cursor().execute(
                    "select * from flywheel_api.resolve_attachment_ticket"
                    "(%s,%s,%s::jsonb)",
                    (ticket, ACTOR, json.dumps(_context(context))),
                ).fetchone()
            return ResolvedAttachment(**row) if row else None
        except Exception as error:
            raise AttachmentRepositoryError("attachment resolve query failed") from error

    def record_access(
        self,
        resolved: ResolvedAttachment,
        result: str,
        context: dict[str, Any],
    ) -> None:
        try:
            with self._connection() as connection:
                connection.cursor().execute(
                    "select flywheel_api.record_attachment_access"
                    "(%s,%s,%s,%s,%s::jsonb)",
                    (
                        resolved.attachment_id,
                        ACTOR,
                        resolved.purpose,
                        result,
                        json.dumps(_context(context)),
                    ),
                )
        except Exception as error:
            raise AttachmentRepositoryError("attachment audit query failed") from error
